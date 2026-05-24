"""16 — WB FULL PIPELINE v2: DOM search + parallel enrichment.

PURPOSE
    Demo-ready end-to-end. Same shape and feature parity as the Ozon
    production scraper:

      1. DOM-scrape WB SPA for top-N product stubs (15_dom_scraper)
      2. For each stub, in parallel via asyncio.gather:
         • card.json   → characteristics + description + imt_id
                         + photo_count + brand + category
         • gallery     → full image URLs (no HEAD-verify; we trust
                         photo_count from card.json — saves N HEADs)
         • feedbacks/v2 → reviews with photo_urls (mini+full+jpg) and
                         video_urls (HLS preview+playlist)

    Output JSON is the same shape that ProductOffer expects in prod,
    so porting to backend/src/pricepulse/scrapers/wb.py is mostly a
    class extract and a model_validate call.

PERFORMANCE
    Cold (first run, includes browser boot): ~6-8 s
    Warm (subsequent search() calls on same browser): ~3-4 s per query
        (≈ 3 s for SPA render + 200 ms parallel enrichment fan-out)

USAGE
    cd wb_research
    uv run python 16_full_pipeline_v2.py "ноутбук" "iphone 15" "шины 205 55 R16"
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

from _common import (
    WB_HEADERS,
    basket_for,
    card_json_url,
    err,
    feedback_photo_urls,
    feedback_video_urls,
    feedbacks_host,
    image_url,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

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

LIMIT = 5
REVIEWS_PER_OFFER = 10


# ---------------------------------------------------------------------------
# DOM extractor — copy of 15's EXTRACTOR_JS so this script is standalone.
# ---------------------------------------------------------------------------
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

  // C) DOM scrape
  try {
    let cards = document.querySelectorAll('article.product-card');
    if (cards.length === 0) cards = document.querySelectorAll('.product-card');
    if (cards.length === 0) cards = document.querySelectorAll('[data-card-index]');
    if (cards.length === 0) cards = document.querySelectorAll('a[href*="/catalog/"][href*="/detail.aspx"]');
    out.debug.dom_cards = cards.length;
    const seen = new Set();
    // Strict, single-match price selectors — DO NOT use [class*=price]
    // (it greedily concatenates final-price + old-price + monthly +
    // WB-wallet badges, producing impossible 53 196 139 990₽ totals).
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
      // Take only the first contiguous digit-group with optional NBSP
      const m = text.match(/(\d[\d  \s]*\d|\d)/);
      if (!m) return null;
      const digits = m[1].replace(/[  \s]/g, '');
      const n = parseInt(digits, 10);
      // Sanity: WB caps at ~5M ₽ for any single SKU; anything bigger
      // means we caught concatenated multi-price text — drop it.
      return (n > 0 && n < 5_000_000) ? n : null;
    }
    for (const card of cards) {
      const a = card.matches('a') ? card : card.querySelector('a[href*="/catalog/"]');
      const url = a ? a.href : '';
      const nmM = url.match(/\/catalog\/(\d+)/);
      if (!nmM) continue;
      const nm = nmM[1];
      if (seen.has(nm)) continue;
      seen.add(nm);
      const nameEl = pickFirst(card, NAME_SELECTORS);
      const name = (nameEl ? nameEl.innerText : (a ? a.innerText : '')).trim();
      const priceEl = pickFirst(card, PRICE_SELECTORS);
      const priceRub = parseRub(priceEl ? priceEl.innerText : '');
      const brandEl = card.querySelector('.product-card__brand');
      const img = card.querySelector('img');
      out.products.push({
        nm: Number(nm), url,
        name: name,
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


# ---------------------------------------------------------------------------
# WB stub normalisation — different DOM sources surface different field
# names. Pull `nm_id`, `name`, `price_rub`, `image`, `brand`, `rating`,
# `feedback_count` and a usable URL. `imt_id` from card.json later.
# ---------------------------------------------------------------------------
def _price_from_stub(p: dict) -> int | None:
    """Best-effort price (rubles) from the various DOM sources.
    Falls back to None — _enrich_one then backfills from card.json
    `extended.clientPriceU` which is the canonical post-discount price."""
    # DOM extractor already sanitised: returns int rubles in `price_rub`
    pr = p.get("price_rub")
    if isinstance(pr, (int, float)) and 0 < pr < 5_000_000:
        return int(pr)
    # Nuxt: sizes[0].price.total (kopeyki → rub)
    sizes = p.get("sizes") or []
    if sizes:
        sp = (sizes[0] or {}).get("price") or {}
        total = sp.get("total") or sp.get("product") or sp.get("basic")
        if isinstance(total, (int, float)) and 0 < total < 5_000_000_00:
            return int(total) // 100
    # JSON-LD: offers.price (rub already)
    if isinstance(p.get("price"), (int, float)) and 0 < p["price"] < 5_000_000:
        return int(p["price"])
    return None


def _normalize_stub(raw: dict) -> dict | None:
    nm = raw.get("nm") or raw.get("id") or raw.get("sku")
    if nm is None:
        return None
    try:
        nm = int(nm)
    except (TypeError, ValueError):
        return None
    return {
        "nm_id":           nm,
        "root":            raw.get("root"),
        "name":            raw.get("name") or raw.get("imt_name"),
        "brand":           raw.get("brand") if isinstance(raw.get("brand"), str)
                          else (raw.get("brand") or {}).get("name"),
        "supplier":        raw.get("supplier"),
        "price":           _price_from_stub(raw),
        "rating":          raw.get("nmReviewRating") or raw.get("reviewRating")
                          or raw.get("rating"),
        "feedbacks":       raw.get("feedbacks") or raw.get("nmFeedbacks") or 0,
        "image":           raw.get("image"),
        "url":             raw.get("url") or f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
    }


# ---------------------------------------------------------------------------
# card.json — chars + description + imt_id + photo_count + category.
# Reuses 02's ±5 shard cascade.
# ---------------------------------------------------------------------------
def _flatten_chars(card: dict) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for grp in card.get("grouped_options") or []:
        gn = (grp.get("group_name") or "").strip()
        for opt in grp.get("options") or []:
            name = (opt.get("name") or "").strip()
            value = str(opt.get("value") or "").strip()
            if name and value:
                out.append((gn, name, value))
    if not out:
        for opt in card.get("options") or []:
            name = (opt.get("name") or "").strip()
            value = str(opt.get("value") or "").strip()
            if name and value:
                out.append(("", name, value))
    return out


async def _fetch_card(client, nm_id: int) -> tuple[dict | None, str | None]:
    primary = int(basket_for(nm_id))
    for delta in (0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
        nn = primary + delta
        if not (1 <= nn <= 60):
            continue
        try:
            r = await client.get(card_json_url(nm_id, shard=f"{nn:02d}"))
        except Exception:
            continue
        if r.status_code == 200 and r.content:
            try:
                return r.json(), f"{nn:02d}"
            except Exception:
                continue
    return None, None


# ---------------------------------------------------------------------------
# feedbacks/v2 with photos + video URL builders.
# ---------------------------------------------------------------------------
def _enrich_review(fb: dict) -> dict:
    out = dict(fb)
    photos: list[dict] = []
    for p in fb.get("photos") or []:
        if not p.get("isReady", True):
            continue
        key = p.get("key")
        if isinstance(key, str) and "/" in key:
            try:
                photos.append(feedback_photo_urls(key))
            except Exception:
                pass
    out["photo_urls"] = photos
    v = fb.get("video")
    if isinstance(v, dict) and v.get("isReady"):
        vid = v.get("id")
        if isinstance(vid, str) and "/" in vid:
            try:
                out["video_urls"] = feedback_video_urls(vid)
            except Exception:
                out["video_urls"] = None
        else:
            out["video_urls"] = None
    else:
        out["video_urls"] = None
    return out


async def _fetch_reviews(client, imt_id: int) -> tuple[dict | None, str | None]:
    primary = feedbacks_host(imt_id)
    fallback = "feedbacks1.wb.ru" if primary == "feedbacks2.wb.ru" else "feedbacks2.wb.ru"
    for ver in ("v2", "v1"):
        for host in (primary, fallback):
            try:
                r = await client.get(f"https://{host}/feedbacks/{ver}/{imt_id}")
            except Exception:
                continue
            if r.status_code == 200 and r.content:
                try:
                    return r.json(), f"{host}/{ver}"
                except Exception:
                    continue
    return None, None


# ---------------------------------------------------------------------------
# DOM browser — copy of 15.WBDomSearch's start/search/stop, slimmed.
# ---------------------------------------------------------------------------
class _DomBrowser:
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

    async def dom_search(
        self,
        query: str,
        *,
        settle_s: float = 3.0,
        deadline_s: float = 12.0,
    ) -> dict:
        if self._tab is None:
            raise RuntimeError("start() not called")
        spa_url = f"{WB_HOME}catalog/0/search.aspx?search={quote(query)}&sort=popular"
        async with self._lock:
            try:
                await self._tab.get(spa_url)
            except Exception as exc:
                return {"error": f"nav failed: {exc}"}
            await asyncio.sleep(settle_s)
            deadline = time.perf_counter() + deadline_s - settle_s
            last: dict = {}
            while time.perf_counter() < deadline:
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
            return last or {"error": "no products"}


# ---------------------------------------------------------------------------
# Enrichment per stub — runs card.json + feedbacks in parallel.
# ---------------------------------------------------------------------------
async def _enrich_one(client, stub: dict) -> dict:
    nm = stub["nm_id"]
    # Phase 1: card.json gives us imt_id, chars, photo_count, brand
    (card, shard) = await _fetch_card(client, nm)

    chars: list[tuple[str, str, str]] = []
    gallery: list[str] = []
    description = ""
    imt_id = stub.get("root")
    brand = stub.get("brand")
    photo_count = 0
    if card:
        chars = _flatten_chars(card)
        photo_count = (card.get("media") or {}).get("photo_count") or 0
        description = (card.get("description") or "")[:1500]
        imt_id = card.get("imt_id") or imt_id
        if not brand:
            brand = ((card.get("selling") or {}).get("brand_name")) or brand
        if shard and photo_count:
            gallery = [image_url(nm, i, shard=shard) for i in range(1, photo_count + 1)]
        # Price backfill — card.json.extended carries the canonical
        # final-price in kopeyki. Use whenever DOM didn't yield a
        # sane rub value.
        if not stub.get("price"):
            ext = card.get("extended") or {}
            for key in ("clientPriceU", "basicPriceU", "discountPriceU"):
                v = ext.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    stub["price"] = int(v) // 100
                    break
            # Some cards put it under sizes/colors
            if not stub.get("price"):
                colors = card.get("colors") or []
                for col in colors:
                    sizes = col.get("sizes") or []
                    for sz in sizes:
                        pr = sz.get("price") or {}
                        total = pr.get("product") or pr.get("basic") or pr.get("total")
                        if isinstance(total, (int, float)) and total > 0:
                            stub["price"] = int(total) // 100
                            break
                    if stub.get("price"):
                        break

    # Phase 2: reviews via imt_id (independent — but we needed imt_id from card)
    reviews: list[dict] = []
    reviews_total = stub.get("feedbacks") or 0
    avg_rating = stub.get("rating") or 0
    reviews_via = None
    if imt_id:
        rev_body, rev_via = await _fetch_reviews(client, int(imt_id))
        reviews_via = rev_via
        if rev_body:
            reviews_total = rev_body.get("feedbackCount") or reviews_total
            try:
                avg_rating = float(rev_body.get("valuation") or avg_rating or 0)
            except (TypeError, ValueError):
                pass
            reviews = [
                _enrich_review(fb)
                for fb in (rev_body.get("feedbacks") or [])[:REVIEWS_PER_OFFER]
            ]

    return {
        **stub,
        "imt_id":         imt_id,
        "brand":          brand,
        "shard":          shard,
        "description":    description,
        "characteristics": chars,
        "gallery":        gallery,
        "photo_count":    photo_count,
        "rating":         avg_rating or None,
        "reviews_total":  reviews_total,
        "reviews":        reviews,
        "reviews_via":    reviews_via,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> int:
    section("WB FULL PIPELINE v2 — DOM search + parallel card+feedbacks enrichment")

    try:
        import httpx
        import nodriver  # noqa: F401
    except ImportError as exc:
        err(f"missing dep: {exc}")
        return 3

    queries = sys.argv[1:] or [query_from_argv()]
    info(f"queries: {queries}")
    info(f"limit per query = {LIMIT}, reviews per offer = {REVIEWS_PER_OFFER}")
    headless = os.environ.get("HEADLESS", "0") == "1"
    info(f"headless = {headless}")

    browser = _DomBrowser(headless=headless)
    all_outcomes: list[dict] = []
    try:
        with _Timer() as t_boot:
            await browser.start()
        ok(f"browser boot = {t_boot.ms} ms\n")

        async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=12) as http:
            for q in queries:
                section(f"query: {q!r}")
                # 1) DOM search → stubs
                with _Timer() as t_search:
                    dom = await browser.dom_search(q)
                if dom.get("error") or not dom.get("products"):
                    err(f"DOM search failed: {dom.get('error')}")
                    all_outcomes.append({"query": q, "error": dom.get("error")})
                    continue
                raws = dom["products"][:LIMIT]
                stubs = [s for s in (_normalize_stub(p) for p in raws) if s]
                info(f"DOM search ok in {t_search.ms} ms — {len(stubs)} stubs via {dom.get('source')}")

                # 2) Enrich each stub in parallel
                with _Timer() as t_enrich:
                    enriched = await asyncio.gather(
                        *(_enrich_one(http, s) for s in stubs),
                        return_exceptions=True,
                    )
                ok_enriched: list[dict] = []
                for e in enriched:
                    if isinstance(e, BaseException):
                        warn(f"  enrich crash: {e!r}")
                        continue
                    ok_enriched.append(e)
                ok(f"enriched {len(ok_enriched)}/{len(stubs)} in {t_enrich.ms} ms (parallel)")

                # 3) Pretty-print each offer
                for i, o in enumerate(ok_enriched, 1):
                    price_rub = o.get("price") or 0
                    rating = o.get("rating") or 0
                    n_chars = len(o.get("characteristics") or [])
                    n_imgs  = len(o.get("gallery") or [])
                    n_revs  = len(o.get("reviews") or [])
                    revs_total = o.get("reviews_total") or 0
                    photos_in_revs = sum(len(r.get("photo_urls") or []) for r in (o.get("reviews") or []))
                    has_video = any(r.get("video_urls") for r in (o.get("reviews") or []))
                    info(
                        f"  [{i}] nm={o.get('nm_id'):<10} ★{rating:.1f}  {price_rub:,}₽".replace(",", " ")
                        + f"   {n_chars:>2} chars | {n_imgs:>2} imgs | "
                        f"{n_revs}/{revs_total} revs ({photos_in_revs} photos{', video' if has_video else ''})"
                    )
                    info(f"      {(o.get('name') or '')[:80]}")

                all_outcomes.append({"query": q, "offers": ok_enriched})
    finally:
        await browser.stop()

    section("Summary")
    enriched_total = sum(len(o.get("offers") or []) for o in all_outcomes)
    ok(f"queries {len(queries)}, enriched offers total = {enriched_total}")

    path = save_json("16_full_pipeline_v2", {"queries": queries, "outcomes": all_outcomes})
    ok(f"saved → {path}")
    info("→ shape is ready for direct mapping to ProductOffer in backend/scrapers/wb.py")
    return 0 if enriched_total else 1


class _Timer:
    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.ms = int((time.perf_counter() - self._t0) * 1000)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
