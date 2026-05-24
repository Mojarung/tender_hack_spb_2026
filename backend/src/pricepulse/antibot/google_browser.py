"""Google Shopping — persistent stealth browser singleton.

Same pattern as `yandex_browser.py` / `wb_browser.py`: one nodriver
Chrome with a per-process persistent profile so Google's anti-bot
cookies survive across requests. We open
``google.com/search?q=<q>&tbm=shop&hl=ru&gl=ru``, run an in-page JS
extractor that pulls product cards (title / price / seller / rating /
image — Google's URL is hidden behind a JS click, so the URL stays
empty for now; see `google_research/` probes 03-04).

Lifecycle: singleton — one browser, one tab, lock-serialised
`shopping_search()` calls. Closed from the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import structlog

log = structlog.get_logger(__name__)

GOOGLE_SHOPPING = (
    "https://www.google.com/search?tbm=shop&hl=ru&gl=ru&q="
)


# Card extractor — same as google_research/04. Walks every <img>, climbs
# to the nearest ancestor whose text contains a ruble price, deduplicates,
# pulls title / price / seller / rating / reviews_count / image. URL is
# left empty (Google fires it via a trusted-click handler — see probes).
EXTRACTOR_JS = r"""
(() => {
  const ruble = /\d[\d\s]*\s*(?:₽|руб)/i;
  const seen = new Set();
  const cards = [];
  for (const img of document.querySelectorAll('img')) {
    const w = img.naturalWidth || img.width || 0;
    if (w && w < 80) continue;
    let el = img;
    for (let d = 0; d < 10 && el; d++) {
      el = el.parentElement;
      if (!el) break;
      if (!ruble.test(el.innerText || '')) continue;
      if ((el.innerText || '').length > 1500) break;
      if (seen.has(el)) break;
      seen.add(el);
      cards.push(el);
      break;
    }
  }
  const out = [];
  const seenTitles = new Set();
  for (const c of cards) {
    const rawTxt = (c.innerText || '').replace(/About this result|Report a violation/g, '').trim();
    const lines = rawTxt.split(/\n+/).map(s => s.trim()).filter(Boolean);
    // Title — longest line that isn't a price / badge / disclaimer
    const badRe = /^(?:Низкая цена|Б\/у|Возврат|Обычно|и не только|и ещ[её])/i;
    let title = '';
    for (const l of lines) {
      if (/^\d/.test(l)) continue;
      if (badRe.test(l)) continue;
      if (l.length > title.length) title = l;
    }
    if (!title || title.length < 4) continue;
    if (seenTitles.has(title)) continue;
    seenTitles.add(title);
    const priceMatch = rawTxt.match(/(\d{1,3}(?:[ \s]\d{3})*(?:,\d+)?)\s*₽/);
    let price = null;
    if (priceMatch) {
      price = priceMatch[1].replace(/[\s ]/g, '').replace(',', '.').split('.')[0];
    }
    let seller = null;
    for (let i = 0; i < lines.length; i++) {
      if (!/₽/.test(lines[i])) continue;
      for (let j = i + 1; j < lines.length; j++) {
        const l = lines[j];
        if (/^Обычно|^Возврат|^\d|и не только/i.test(l)) continue;
        if (l.length > 40) continue;
        seller = l; break;
      }
      break;
    }
    const rm = rawTxt.match(/(\d[.,]\d)\s*\(([^)]+)\)/);
    const rating = rm ? parseFloat(rm[1].replace(',', '.')) : null;
    let reviews_count = null;
    if (rm) {
      const cm = rm[2].match(/(\d+(?:[.,]\d+)?)\s*(тыс|тысяч|k|K)?/);
      if (cm) {
        let n = parseFloat(cm[1].replace(',', '.'));
        if (cm[2]) n *= 1000;
        reviews_count = Math.round(n);
      }
    }
    // Image: prefer http (real gstatic CDN), fall back to inline base64
    // (Google ships base64 thumbs immediately + replaces them with the
    // gstatic versions only after first visible scroll — the data: ones
    // render fine in <img>, just smaller).
    let image = null;
    const imgs = Array.from(c.querySelectorAll('img'));
    const httpImg = imgs.find(i => {
      const s = i.getAttribute('src') || '';
      return s.startsWith('http') && !/yastatic|favicon/i.test(s);
    });
    if (httpImg) image = httpImg.getAttribute('src');
    else {
      const dataImg = imgs.find(i => (i.getAttribute('src') || '').startsWith('data:image'));
      if (dataImg) image = dataImg.getAttribute('src');
    }
    // Extra metadata pulled from the same card text — feeds the dynamic
    // facets on the frontend (UI lets the user filter by these).
    const chars = {};
    if (/НИЗКАЯ ЦЕНА/i.test(rawTxt)) chars["Метка"] = "Низкая цена";
    if (/Б\/у/i.test(rawTxt)) chars["Состояние"] = "Б/у";
    const original = rawTxt.match(/Обычно\s+(\d{1,3}(?:[ \s]\d{3})*)\s*₽/);
    if (original) chars["Обычная цена"] = original[1].replace(/[\s ]/g, '') + " ₽";
    const delivery = rawTxt.match(/Возврат в течение[^\n]+/);
    if (delivery) chars["Доставка"] = delivery[0].slice(0, 60);
    // Brand — first word of the title if it's a known maker
    const BRANDS_RE = new RegExp(
      '\\b(Apple|Samsung|Xiaomi|Huawei|Sony|JBL|Marshall|Bose|LG|Asus|'
      + 'Lenovo|HP|Dell|Acer|MSI|Logitech|Razer|Beats|Sennheiser|AKG|'
      + 'Realme|Honor|Nothing|OPPO|Vivo|TCL|Philips|Bosch|Siemens|'
      + 'Indesit|Электролюкс)\\b', 'i'
    );
    const brandMatch = title.match(BRANDS_RE);
    if (brandMatch) chars["Бренд"] = brandMatch[1];

    if (!price) continue;     // ProductOffer.price is required
    if (!seller) continue;    // no seller ⇒ likely an expanded-view dupe
    out.push({title, price, seller, rating, reviews_count, image, chars});
    if (out.length >= 12) break;
  }
  return JSON.stringify(out);
})()
"""


class GoogleBrowserSearch:
    """Persistent nodriver browser dedicated to Google Shopping scraping."""

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
    async def create(cls, settings: Any) -> GoogleBrowserSearch:
        profile = (
            getattr(settings, "google_profile_dir", None)
            or "var/profiles/google"
        )

        def _resolve() -> str:
            p = Path(profile)
            p.mkdir(parents=True, exist_ok=True)
            return str(p.resolve())

        user_data_dir = await asyncio.to_thread(_resolve)
        return cls(
            headless=getattr(settings, "browser_headless", True),
            user_data_dir=user_data_dir,
            browser_executable_path=getattr(settings, "google_browser_path", "")
            or getattr(settings, "yandex_browser_path", "")
            or getattr(settings, "wb_browser_path", "")
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
                raise RuntimeError("nodriver is required for the Google path") from exc

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
            # Warmup nav: google.com home. Pre-warms cookies + clears the
            # "set Russian as preferred language" interstitial on a fresh profile.
            self._tab = await self._browser.get("https://www.google.com/")
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
                log.warning("google_browser.stealth_init_failed", error=str(exc))
            log.info(
                "google_browser.started",
                headless=self._headless, user_data_dir=self._user_data_dir,
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
                    log.warning("google_browser.reset_stop_failed", error=str(exc))
                self._browser = None
                self._tab = None
                log.info("google_browser.reset")

    async def shopping_search(
        self,
        query: str,
        *,
        settle_s: float = 4.0,
    ) -> dict[str, Any]:
        """Navigate to Google Shopping for ``query`` and pull the card
        list via the in-page extractor. Returns
        ``{"products": [...], "source": "google-shopping"}`` or
        ``{"error": str}``.
        """
        await self._ensure_started()
        url = f"{GOOGLE_SHOPPING}{quote_plus(query)}"
        async with self._lock:
            try:
                await self._tab.get(url)
            except BaseException as exc:
                if self._is_dead_browser_error(exc):
                    log.warning("google_browser.dead_browser_on_nav", error=repr(exc))
                    await self._reset()
                    await self._ensure_started()
                    try:
                        await self._tab.get(url)
                    except Exception as exc2:
                        return {"error": f"nav failed after reset: {exc2}"}
                else:
                    return {"error": f"nav failed: {exc}"}

            await asyncio.sleep(settle_s)
            # Google lazy-loads the real gstatic thumbnail images — the
            # initial paint only ships base64 placeholders. Scroll the
            # viewport down once to trigger the IntersectionObserver
            # that swaps them in, then wait a beat.
            try:
                await self._tab.evaluate("window.scrollTo(0, 1200)", await_promise=False)
                await asyncio.sleep(1.5)
                await self._tab.evaluate("window.scrollTo(0, 0)", await_promise=False)
                await asyncio.sleep(0.5)
            except Exception as exc:
                log.debug("google_browser.scroll_warmup_failed", error=str(exc))
            try:
                raw = await self._tab.evaluate(EXTRACTOR_JS, await_promise=False)
            except Exception as exc:
                return {"error": f"extractor failed: {exc}"}
            try:
                products = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except json.JSONDecodeError as exc:
                return {"error": f"extractor non-json: {exc}"}
            if not isinstance(products, list):
                products = []
            log.info(
                "google_browser.shopping_ok",
                query=query, returned=len(products),
            )
            return {"products": products, "source": "google-shopping"}

    async def aclose(self) -> None:
        async with self._init_lock:
            if self._browser is not None:
                try:
                    self._browser.stop()
                except Exception as exc:
                    log.warning("google_browser.aclose_stop_failed", error=str(exc))
            self._browser = None
            self._tab = None


_singleton: GoogleBrowserSearch | None = None
_singleton_lock = asyncio.Lock()


async def get_google_browser() -> GoogleBrowserSearch:
    global _singleton
    async with _singleton_lock:
        if _singleton is None:
            from pricepulse.config import get_settings

            _singleton = await GoogleBrowserSearch.create(get_settings())
        return _singleton


async def close_google_browser() -> None:
    global _singleton
    async with _singleton_lock:
        if _singleton is not None:
            await _singleton.aclose()
            _singleton = None


__all__ = [
    "GoogleBrowserSearch",
    "close_google_browser",
    "get_google_browser",
]
