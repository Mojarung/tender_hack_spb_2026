"""15 — WB DOM scraper (the bulletproof fallback).

PURPOSE
    Empirical finding (May 2026): WB's catalog/search page is
    server-side rendered via Nuxt. The product list is baked into the
    HTML at request time and the SPA hydrates from `window.__NUXT__`.
    There is NO separate XHR/fetch to search.wb.ru during the initial
    page load — which is why the CDP Network observer in 14 returned
    zero captured responses despite products being visible.

    This script reads products three ways, in order of richness:
      A) `window.__NUXT__` — Nuxt's hydration payload; full product
         objects with all fields the API would return
      B) `<script type="application/ld+json">` — JSON-LD ItemList /
         Product blocks WB emits for SEO
      C) DOM scrape — `<article class="product-card">` children, last
         resort, extracts name/price/url/brand/image only

    All three live in HTML — no API, no PoW, no captcha. Slower than
    API (~2-3 sec per query for page render) but bulletproof.

USAGE
    cd wb_research
    uv run python 15_dom_scraper.py "ноутбук" "iphone 15"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import err, info, ok, save_json, section, warn

if sys.platform == "win32":
    warnings.filterwarnings("ignore", category=ResourceWarning)
    _orig_unraisable = sys.unraisablehook

    def _quiet_unraisable(unraisable, *, _orig=_orig_unraisable):
        exc = unraisable.exc_value
        if isinstance(exc, ValueError) and "closed pipe" in str(exc):
            return
        _orig(unraisable)

    sys.unraisablehook = _quiet_unraisable


WB_HOME = "https://www.wildberries.ru/"
PROFILE_DIR = Path(__file__).parent / ".profile_wb"


# Three-layer extractor: Nuxt state → JSON-LD → DOM scrape.
# Runs entirely inside the browser and returns one JSON blob with the
# results from whichever layer worked.
EXTRACTOR_JS = r"""
(() => {
  const out = {source: null, products: [], debug: {}};

  // ───────── A) Nuxt hydration payload ─────────
  try {
    const nuxt = window.__NUXT__ || window.__NUXT_DATA__ || window.__INITIAL_STATE__;
    out.debug.has_nuxt = !!nuxt;
    if (nuxt) {
      // Walk nuxt.state / nuxt.data looking for arrays of objects that
      // look like products (have an `id` and `name`).
      const seen = new Set();
      const collect = (node, depth = 0) => {
        if (depth > 8 || !node) return;
        if (Array.isArray(node)) {
          // Is this an array of product-like objects?
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
      if (out.products.length > 0) {
        out.source = 'nuxt';
        return JSON.stringify(out);
      }
    }
  } catch (e) { out.debug.nuxt_error = String(e); }

  // ───────── B) JSON-LD ItemList ─────────
  try {
    const ldNodes = document.querySelectorAll('script[type="application/ld+json"]');
    out.debug.ld_blocks = ldNodes.length;
    for (const node of ldNodes) {
      let payload;
      try { payload = JSON.parse(node.textContent || '{}'); }
      catch (e) { continue; }
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
    if (out.products.length > 0) {
      out.source = 'json-ld';
      return JSON.stringify(out);
    }
  } catch (e) { out.debug.ld_error = String(e); }

  // ───────── C) DOM scrape ─────────
  try {
    // WB has used several class hierarchies for product cards over the
    // years; we try a few generic ones and pick the first that matches.
    let cards = document.querySelectorAll('article.product-card');
    if (cards.length === 0) cards = document.querySelectorAll('.product-card');
    if (cards.length === 0) cards = document.querySelectorAll('[data-card-index]');
    if (cards.length === 0) cards = document.querySelectorAll('a[href*="/catalog/"][href*="/detail.aspx"]');
    out.debug.dom_cards = cards.length;

    const seen = new Set();
    for (const card of cards) {
      const a = card.matches('a') ? card : card.querySelector('a[href*="/catalog/"]');
      const url = a ? a.href : '';
      const nmM = url.match(/\/catalog\/(\d+)/);
      if (!nmM) continue;
      const nm = nmM[1];
      if (seen.has(nm)) continue;
      seen.add(nm);
      const name = (
        card.querySelector('.product-card__name, .goods-name, [class*="name"]')
        || {}).innerText || (a ? a.innerText : '');
      const priceEl = card.querySelector(
        '.price__lower-price, .price-block__final-price, [class*="price"]'
      );
      const brandEl = card.querySelector('.product-card__brand, [class*="brand"]');
      const img = card.querySelector('img');
      out.products.push({
        nm: Number(nm), url,
        name: (name || '').trim(),
        price: (priceEl ? priceEl.innerText : '').trim(),
        brand: (brandEl ? brandEl.innerText : '').trim(),
        image: img ? (img.src || img.dataset.src || '') : '',
      });
    }
    if (out.products.length > 0) {
      out.source = 'dom';
      return JSON.stringify(out);
    }
  } catch (e) { out.debug.dom_error = String(e); }

  return JSON.stringify(out);
})()
"""


class WBDomSearch:
    """Persistent browser tab that scrapes WB's SSR-rendered search
    page. Same lifecycle as 14 (start/search/stop)."""

    def __init__(self, *, headless: bool = False) -> None:
        self._headless = headless
        self._browser: Any = None
        self._tab: Any = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._browser is not None:
            return
        import nodriver as uc

        PROFILE_DIR.mkdir(exist_ok=True)
        self._browser = await uc.start(
            headless=self._headless,
            user_data_dir=str(PROFILE_DIR.resolve()),
            lang="ru-RU",
            browser_args=[
                "--lang=ru-RU",
                "--accept-lang=ru-RU,ru;q=0.9",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        self._tab = await self._browser.get(WB_HOME)
        try:
            await self._tab.send(
                uc.cdp.page.add_script_to_evaluate_on_new_document(
                    source=(
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{ get: () => undefined });"
                    ),
                ),
            )
        except Exception:
            pass

    async def stop(self) -> None:
        async with self._lock:
            if self._browser is None:
                return
            try:
                self._browser.stop()
            except Exception:
                pass
            self._browser = None
            self._tab = None

    async def search(
        self,
        query: str,
        *,
        wait_for_render_s: float = 3.0,
        max_total_wait_s: float = 12.0,
    ) -> dict[str, Any]:
        if self._tab is None:
            raise RuntimeError("WBDomSearch.start() not called")

        spa_url = (
            f"{WB_HOME}catalog/0/search.aspx?search={quote(query)}&sort=popular"
        )
        async with self._lock:
            try:
                await self._tab.get(spa_url)
            except Exception as exc:
                return {"error": f"navigation failed: {exc}"}

            # Give the SPA an initial moment to render.
            await asyncio.sleep(wait_for_render_s)

            deadline = time.perf_counter() + max_total_wait_s - wait_for_render_s
            last_result: dict[str, Any] = {}
            while time.perf_counter() < deadline:
                try:
                    raw = await self._tab.evaluate(EXTRACTOR_JS, await_promise=False)
                except Exception as exc:
                    return {"error": f"evaluate failed: {exc}"}
                if isinstance(raw, str) and raw:
                    try:
                        last_result = json.loads(raw)
                    except json.JSONDecodeError:
                        last_result = {"error": f"non-json from extractor: {raw[:200]}"}
                products = last_result.get("products") or []
                if products:
                    return {
                        "status": 200,
                        "source": last_result.get("source"),
                        "products": products,
                        "debug": last_result.get("debug") or {},
                    }
                await asyncio.sleep(0.5)
            return {
                "status": None,
                "error": "no products in DOM/Nuxt/JSON-LD after settle",
                "debug": last_result.get("debug") or {},
            }


async def main() -> int:
    section("WB DOM SCRAPER — Nuxt + JSON-LD + DOM (SSR-friendly)")

    try:
        import nodriver  # noqa: F401
    except ImportError:
        err("nodriver not installed — `uv sync` in wb_research/")
        return 3

    queries = sys.argv[1:] or ["ноутбук", "iphone 15", "шины 205 55 R16"]
    info(f"queries: {queries}")
    headless = os.environ.get("HEADLESS", "0") == "1"
    info(f"headless = {headless}")

    s = WBDomSearch(headless=headless)
    outcomes: list[dict] = []
    try:
        with _Timer() as t_boot:
            await s.start()
        ok(f"browser boot = {t_boot.ms} ms\n")

        for q in queries:
            with _Timer() as t_q:
                res = await s.search(q)
            elapsed = t_q.ms
            products = res.get("products") or []
            n = len(products)
            source = res.get("source")
            outcome = "ok" if n > 0 else "empty"
            mark = "+" if outcome == "ok" else "-"
            print(f"  {mark} {q!r:<40} via {source or '-':<8} ({elapsed:>5} ms) → {n} products")
            for p in products[:3]:
                info(
                    f"      nm={p.get('nm') or p.get('id') or '-'}  "
                    f"{(p.get('name') or '')[:65]}"
                )
            if not n:
                warn(f"    debug: {res.get('debug')}")
                warn(f"    error: {res.get('error')}")

            outcomes.append({
                "query": q, "status": res.get("status"), "n": n,
                "elapsed_ms": elapsed, "source": source,
                "debug": res.get("debug"), "first": products[:3],
            })
    finally:
        await s.stop()

    section("Summary")
    all_ok = all(o["status"] == 200 and o["n"] > 0 for o in outcomes)
    if all_ok:
        ok("EVERY query yielded products — DOM/Nuxt path works")
        ok("→ port WBDomSearch class into backend/src/pricepulse/scrapers/wb.py")
        ok(f"→ data source used most often: {next(o['source'] for o in outcomes)}")
    else:
        bad = [o for o in outcomes if o["status"] != 200 or o["n"] == 0]
        warn(f"{len(bad)} queries returned nothing — see debug field in saved JSON")

    save_json("15_dom_scraper", {"queries": queries, "outcomes": outcomes})
    return 0 if all_ok else 1


class _Timer:
    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.ms = int((time.perf_counter() - self._t0) * 1000)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
