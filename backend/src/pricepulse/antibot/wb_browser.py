"""WB browser-driven search — DOM-scrape the SSR-rendered SPA.

Why a browser at all? `search.wb.ru/v18` is behind WB Page Guard (PG-41:
TLS sig check, PG-42: per-request PoW token). Both reject every
HTTP-only client even with warmed cookies. Reverse-engineering the PoW
algorithm is days of work.

WB's catalog page is **server-side rendered** (Nuxt). Navigating
``wildberries.ru/catalog/0/search.aspx?search=<q>`` returns fully
populated HTML — no API call needed. We let the browser do the nav and
scrape the products three ways in order of richness:

  A) ``window.__NUXT__`` hydration payload — full product objects (`id`,
     `name`, `brand`, `sizes[*].price`, `rating`, `feedbacks`, `root`...)
  B) ``<script type="application/ld+json">`` ItemList — fallback if Nuxt
     state got pruned
  C) DOM ``<article class="product-card">`` scrape — last resort, only
     `nm/name/url/image/price/brand`

Live-validated May 2026 via wb_research/15_dom_scraper.py + 16_full_
pipeline_v2.py: 5/5 stubs per query, cold ~3 s, warm ~0.6 s for
follow-up enrichment.

Lifecycle: singleton — one nodriver browser, persistent profile under
``settings.wb_profile_dir``. Per-request the lock serialises `dom_search`
calls on the single tab.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import structlog

log = structlog.get_logger(__name__)

WB_HOME = "https://www.wildberries.ru/"

# Three-layer DOM extractor — same as wb_research/15_dom_scraper.py.
EXTRACTOR_JS = r"""
(() => {
  const out = {source: null, products: [], debug: {}};

  // A) Nuxt hydration payload
  try {
    const nuxt = window.__NUXT__ || window.__NUXT_DATA__ || window.__INITIAL_STATE__;
    out.debug.has_nuxt = !!nuxt;
    if (nuxt) {
      const seen = new Set();
      const collect = (node, depth = 0) => {
        if (depth > 8 || !node) return;
        if (Array.isArray(node)) {
          if (node.length > 0 && typeof node[0] === 'object' && node[0] !== null
              && node[0].id !== undefined && (node[0].name || node[0].brand)) {
            for (const p of node) {
              if (typeof p !== 'object' || !p) continue;
              const key = String(p.id);
              if (seen.has(key)) continue;
              seen.add(key);
              out.products.push(p);
            }
            return;
          }
          for (const it of node) collect(it, depth + 1);
        } else if (typeof node === 'object') {
          for (const v of Object.values(node)) collect(v, depth + 1);
        }
      };
      collect(nuxt);
      if (out.products.length > 0) { out.source = 'nuxt'; return JSON.stringify(out); }
    }
  } catch (e) { out.debug.nuxt_error = String(e); }

  // B) JSON-LD ItemList
  try {
    const ldNodes = document.querySelectorAll('script[type="application/ld+json"]');
    out.debug.ld_blocks = ldNodes.length;
    for (const node of ldNodes) {
      let payload;
      try { payload = JSON.parse(node.textContent || '{}'); } catch (e) { continue; }
      const items = (payload['@graph'] || (Array.isArray(payload) ? payload : [payload]));
      for (const it of items) {
        if (it && it['@type'] === 'ItemList' && Array.isArray(it.itemListElement)) {
          for (const el of it.itemListElement) {
            const p = el.item || el;
            if (!p || !p.name) continue;
            out.products.push({
              name: p.name, url: p.url, image: p.image,
              brand: (p.brand && p.brand.name) || p.brand,
              sku: p.sku, price: p.offers && p.offers.price,
            });
          }
        }
      }
    }
    if (out.products.length > 0) { out.source = 'json-ld'; return JSON.stringify(out); }
  } catch (e) { out.debug.ld_error = String(e); }

  // C) DOM scrape — strict price selectors (no [class*=price] greedy match)
  try {
    let cards = document.querySelectorAll('article.product-card');
    if (cards.length === 0) cards = document.querySelectorAll('.product-card');
    if (cards.length === 0) cards = document.querySelectorAll('[data-card-index]');
    if (cards.length === 0) cards = document.querySelectorAll('a[href*="/catalog/"][href*="/detail.aspx"]');
    out.debug.dom_cards = cards.length;
    const PRICE_SELECTORS = [
      '.price__lower-price',
      '.price-block__final-price',
      'ins.price-block__final-price',
      '.product-card__price ins',
    ];
    const NAME_SELECTORS = [
      '.product-card__name',
      '.goods-name',
      '.product-card__brand-name',
    ];
    function pickFirst(card, selectors) {
      for (const sel of selectors) {
        const el = card.querySelector(sel);
        if (el) return el;
      }
      return null;
    }
    function parseRub(text) {
      if (!text) return null;
      const m = text.match(/(\d[\d  \s]*\d|\d)/);
      if (!m) return null;
      const digits = m[1].replace(/[  \s]/g, '');
      const n = parseInt(digits, 10);
      return (n > 0 && n < 5_000_000) ? n : null;
    }
    const seen = new Set();
    for (const card of cards) {
      const a = card.matches('a') ? card : card.querySelector('a[href*="/catalog/"]');
      const url = a ? a.href : '';
      const nmM = url.match(/\/catalog\/(\d+)/);
      if (!nmM) continue;
      const nm = nmM[1];
      if (seen.has(nm)) continue;
      const nameEl = pickFirst(card, NAME_SELECTORS);
      // Reject cards that don't carry a real product-name element —
      // WB layout includes recommendation/sponsor tiles that share the
      // `.product-card` class but only contain images + nav. Without
      // this filter we ship empty-name offers (14 of 16 cards observed
      // for an "iphone 15" search were noise tiles).
      if (!nameEl) continue;
      const rawName = nameEl.innerText.trim();
      const cleanName = rawName.replace(/^[\/\\\s|·•·]+/, '').trim();
      if (!cleanName || cleanName.length < 3) continue;
      seen.add(nm);
      const priceEl = pickFirst(card, PRICE_SELECTORS);
      const priceRub = parseRub(priceEl ? priceEl.innerText : '');
      const brandEl = card.querySelector('.product-card__brand');
      const img = card.querySelector('img');
      out.products.push({
        nm: Number(nm), url,
        name: cleanName,
        price_rub: priceRub,
        brand: (brandEl ? brandEl.innerText : '').trim(),
        image: img ? (img.src || img.dataset.src || '') : '',
      });
    }
    if (out.products.length > 0) { out.source = 'dom'; return JSON.stringify(out); }
  } catch (e) { out.debug.dom_error = String(e); }

  return JSON.stringify(out);
})()
"""


class WBBrowserSearch:
    """Persistent nodriver browser dedicated to WB catalog scraping.

    Separate from antibot/browser_pool (which is Ozon-only right now)
    until the hybrid pool refactor. Same lifecycle:
        s = await get_wb_browser()
        result = await s.dom_search("ноутбук")
        ...
        await close_wb_browser()   # called from FastAPI lifespan
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        user_data_dir: str | None = None,
        browser_executable_path: str | None = None,
    ) -> None:
        self._headless = headless
        self._user_data_dir = user_data_dir
        self._browser_exec = browser_executable_path or None
        self._browser: Any = None
        self._tab: Any = None
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()

    @classmethod
    async def create(cls, settings: Any) -> WBBrowserSearch:
        profile = getattr(settings, "wb_profile_dir", None) or "var/profiles/wb"

        def _resolve() -> str:
            p = Path(profile)
            p.mkdir(parents=True, exist_ok=True)
            return str(p.resolve())

        user_data_dir = await asyncio.to_thread(_resolve)
        return cls(
            headless=getattr(settings, "browser_headless", True),
            user_data_dir=user_data_dir,
            browser_executable_path=getattr(settings, "wb_browser_path", "")
            or getattr(settings, "ozon_browser_path", "")
            or None,
        )

    async def _ensure_started(self) -> None:
        if self._browser is not None and self._tab is not None:
            return
        async with self._init_lock:
            if self._browser is not None and self._tab is not None:
                return
            try:
                import nodriver as uc
            except ImportError as exc:    # pragma: no cover
                raise RuntimeError("nodriver is required for the WB path") from exc

            kwargs: dict[str, Any] = {
                "headless": self._headless,
                "lang": "ru-RU",
                "browser_args": [
                    "--lang=ru-RU",
                    "--accept-lang=ru-RU,ru;q=0.9",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }
            if self._user_data_dir:
                kwargs["user_data_dir"] = self._user_data_dir
            if self._browser_exec:
                kwargs["browser_executable_path"] = self._browser_exec

            self._browser = await uc.start(**kwargs)
            self._tab = await self._browser.get(WB_HOME)
            # Minimal stealth — webdriver flag is the only WB-detected one
            try:
                await self._tab.send(
                    uc.cdp.page.add_script_to_evaluate_on_new_document(
                        source=(
                            "Object.defineProperty(navigator, 'webdriver', "
                            "{ get: () => undefined });"
                        ),
                    ),
                )
            except Exception as exc:
                log.warning("wb_browser.stealth_init_failed", error=str(exc))
            log.info(
                "wb_browser.started",
                headless=self._headless,
                user_data_dir=self._user_data_dir,
            )

    def _is_dead_browser_error(self, exc: BaseException) -> bool:
        name = type(exc).__name__
        if name in {"ConnectionClosedError", "ConnectionClosed", "ConnectionClosedOK"}:
            return True
        s = str(exc).lower()
        return any(
            tok in s for tok in (
                "browser has closed", "connection closed",
                "remote endpoint closed", "websocket is closed",
            )
        )

    async def _reset(self) -> None:
        async with self._init_lock:
            if self._browser is not None:
                try:
                    self._browser.stop()
                except Exception as exc:
                    log.warning("wb_browser.reset_stop_failed", error=str(exc))
                self._browser = None
                self._tab = None
                log.info("wb_browser.reset")

    async def dom_search(
        self,
        query: str,
        *,
        settle_s: float = 4.0,
        deadline_s: float = 15.0,
    ) -> dict[str, Any]:
        """Navigate the SPA search page + read products from DOM.

        Returns ``{"source": "nuxt"|"json-ld"|"dom", "products": [...]}``
        on success, or ``{"error": str, "debug": {...}}`` on failure.
        Auto-recovers from a dead browser (user closed window) by
        relaunching once."""
        await self._ensure_started()
        spa_url = (
            f"{WB_HOME}catalog/0/search.aspx?search={quote(query)}&sort=popular"
        )
        async with self._lock:
            try:
                await self._tab.get(spa_url)
            except BaseException as exc:
                if self._is_dead_browser_error(exc):
                    log.warning("wb_browser.dead_browser_on_nav", error=repr(exc))
                    await self._reset()
                    await self._ensure_started()
                    try:
                        await self._tab.get(spa_url)
                    except Exception as exc2:
                        return {"error": f"nav failed after reset: {exc2}"}
                else:
                    return {"error": f"nav failed: {exc}"}

            await asyncio.sleep(settle_s)

            import time as _time

            deadline = _time.perf_counter() + (deadline_s - settle_s)
            last: dict[str, Any] = {}
            while _time.perf_counter() < deadline:
                try:
                    raw = await self._tab.evaluate(EXTRACTOR_JS, await_promise=False)
                except Exception as exc:
                    return {"error": f"evaluate failed: {exc}"}
                if isinstance(raw, str) and raw:
                    try:
                        last = json.loads(raw)
                    except json.JSONDecodeError:
                        pass
                if last.get("products"):
                    return last
                await asyncio.sleep(0.4)
            return last or {"error": "no products in DOM/Nuxt/JSON-LD"}

    async def aclose(self) -> None:
        async with self._init_lock:
            if self._browser is None:
                return
            try:
                self._browser.stop()
            except Exception as exc:
                log.warning("wb_browser.close_failed", error=str(exc))
            self._browser = None
            self._tab = None


_singleton: WBBrowserSearch | None = None
_singleton_lock = asyncio.Lock()


async def get_wb_browser() -> WBBrowserSearch:
    """Process-wide lazy singleton — same shape as `get_browser_pool`
    for Ozon. Closed from the FastAPI lifespan via `close_wb_browser`."""
    global _singleton
    async with _singleton_lock:
        if _singleton is None:
            from pricepulse.config import get_settings

            _singleton = await WBBrowserSearch.create(get_settings())
        return _singleton


async def close_wb_browser() -> None:
    global _singleton
    async with _singleton_lock:
        if _singleton is not None:
            await _singleton.aclose()
            _singleton = None


__all__ = ["WBBrowserSearch", "close_wb_browser", "get_wb_browser"]
