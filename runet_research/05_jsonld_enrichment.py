"""05 — Enrichment: fetch product pages, parse JSON-LD Product blocks.

For each offer URL from probe 04, GET it via curl_cffi (chrome JA3),
parse <script type="application/ld+json">, extract Product fields:
name, image, price, brand, description, rating, reviewCount.

Concurrent fan-out (asyncio.gather, bounded by semaphore). Same pattern
the production runet.py already uses.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).parent))

import nodriver as uc
from curl_cffi.requests import AsyncSession

from _common import (
    PROFILE_DIR, info, ok, query_from_argv, save_json, section, warn,
)


# Reuse the same SERP extractor as probe 04 (in-process; keep this file
# self-contained for the research sandbox — production refactor later).
EXTRACTOR_JS = r"""
(() => {
  const items = Array.from(document.querySelectorAll('li.serp-item, .serp-item'));
  const out = [];
  for (const it of items) {
    const titleEl = it.querySelector('.OrganicTitle h2, .OrganicTitle, h2, h3');
    const linkEl  = it.querySelector('.OrganicTitle-Link, a.Link[href], a[href]');
    if (!titleEl || !linkEl) continue;
    const title = (titleEl.innerText || titleEl.textContent || '').trim();
    if (!title || title.length < 3) continue;
    let href = linkEl.getAttribute('href') || '';
    if (!href) continue;
    if (/yabs\.yandex\.ru/i.test(href)) {
      const pathEl = it.querySelector('.OrganicSubtitle-Path, .Path .Link, .Path');
      if (pathEl) {
        const realPath = (pathEl.innerText || '').trim();
        if (/^https?:\/\//i.test(realPath)) {
          href = realPath;
        } else if (realPath) {
          const host = realPath.split(/[›·•·\s]/)[0].trim();
          if (host && host.includes('.')) href = "https://" + host;
        }
      }
    }
    let rating = null, reviews_count = null;
    const ratingEl = it.querySelector('[class*=rating i], [class*=Rating]');
    if (ratingEl) {
      const text = (ratingEl.innerText || '').replace(/\s+/g, ' ').trim();
      const rm = text.match(/(\d(?:[.,]\d)?)\s*(?:из\s*5)?/);
      if (rm) rating = parseFloat(rm[1].replace(',', '.'));
      const cm = text.match(/(\d+(?:[.,]\d+)?)\s*([KkКк])?\s*(?:отзыв|оценк|review)/i);
      if (cm) {
        let n = parseFloat(cm[1].replace(',', '.'));
        if (cm[2]) n *= 1000;
        reviews_count = Math.round(n);
      }
    }
    const has_cart = !!it.querySelector('[class*=cart i], [class*=shop i], [class*=basket i]');
    if (!has_cart && !rating) continue;
    out.push({title, url: href, rating, reviews_count, has_cart});
  }
  return JSON.stringify(out);
})()
"""


SKIP_HOSTS = re.compile(
    r"(^|\.)(yandex\.ru|ya\.ru|wildberries\.ru|ozon\.ru)$|market\.yandex\.ru",
)


def shop_of(url: str) -> str:
    try:
        return re.sub(r"^www\.", "", (urlparse(url).hostname or "").lower())
    except Exception:
        return ""


_LD_BLOCK_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _walk(node: Any) -> list[dict[str, Any]]:
    """Recursively pull every dict whose @type is 'Product' (case-insensitive)."""
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, str) and t.lower() == "product":
            out.append(node)
        elif isinstance(t, list) and any(isinstance(x, str) and x.lower() == "product" for x in t):
            out.append(node)
        for v in node.values():
            out.extend(_walk(v))
        # @graph wrapper
        for v in (node.get("@graph") or []):
            out.extend(_walk(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_walk(v))
    return out


def parse_jsonld(html: str) -> dict[str, Any] | None:
    """Find the first Product JSON-LD block. Returns flat dict or None."""
    for m in _LD_BLOCK_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        products = _walk(data)
        if not products:
            continue
        p = products[0]
        # Price normalisation
        offers = p.get("offers")
        price = None
        if isinstance(offers, dict):
            price = offers.get("price") or offers.get("lowPrice")
        elif isinstance(offers, list) and offers:
            o0 = offers[0] if isinstance(offers[0], dict) else {}
            price = o0.get("price") or o0.get("lowPrice")
        image = p.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url") or image.get("@id")
        brand = p.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        rating_node = p.get("aggregateRating") or {}
        rating = rating_node.get("ratingValue") if isinstance(rating_node, dict) else None
        review_count = rating_node.get("reviewCount") or rating_node.get("ratingCount") \
            if isinstance(rating_node, dict) else None
        return {
            "name": p.get("name"),
            "price": str(price) if price is not None else None,
            "image": image if isinstance(image, str) else None,
            "brand": brand if isinstance(brand, str) else None,
            "description": (p.get("description") or "")[:500] if p.get("description") else None,
            "rating": float(rating) if rating else None,
            "reviews_count": int(review_count) if review_count else None,
        }
    return None


_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


async def enrich_one(
    session: AsyncSession, sem: asyncio.Semaphore, offer: dict[str, Any],
) -> dict[str, Any]:
    """Fetch offer["url"], parse JSON-LD. Returns the offer with new fields
    merged in (jsonld fields override SERP if present)."""
    async with sem:
        try:
            r = await session.get(offer["url"], timeout=10, allow_redirects=True)
        except Exception as exc:
            offer["enrich_error"] = f"transport: {exc}"
            return offer
        if r.status_code != 200:
            offer["enrich_error"] = f"http {r.status_code}"
            return offer
        try:
            html = r.text
        except Exception:
            html = r.content.decode("utf-8", errors="replace")
        ld = parse_jsonld(html)
        if not ld:
            offer["enrich_error"] = "no jsonld product"
            return offer
        # Merge — jsonld overrides serp fields where present
        for k, v in ld.items():
            if v is not None:
                offer[k] = v
        offer["enrich_ok"] = True
    return offer


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"YANDEX SERP + JSON-LD ENRICH — {query!r}")

    browser = await uc.start(
        headless=False, lang="ru-RU",
        user_data_dir=str(PROFILE_DIR.resolve()),
        browser_args=["--lang=ru-RU", "--window-size=1600,1000"],
    )
    try:
        url = f"https://yandex.ru/search/?text={quote_plus(query)}"
        info(f"SERP: {url}")
        tab = await browser.get(url)
        await asyncio.sleep(5)
        raw = await tab.evaluate(EXTRACTOR_JS, await_promise=False)
        offers_raw = json.loads(raw) if isinstance(raw, str) else raw
    finally:
        browser.stop()

    seen = set()
    offers = []
    for o in offers_raw:
        shop = shop_of(o["url"])
        if not shop or SKIP_HOSTS.search(shop):
            continue
        if (shop, o["title"][:60].lower()) in seen:
            continue
        seen.add((shop, o["title"][:60].lower()))
        o["shop"] = shop
        offers.append(o)
    info(f"raw={len(offers_raw)}  clean={len(offers)}, enriching…")

    sem = asyncio.Semaphore(6)
    async with AsyncSession(impersonate="chrome", headers=_FETCH_HEADERS) as session:
        enriched = await asyncio.gather(*[enrich_one(session, sem, o) for o in offers])

    n_ok = sum(1 for o in enriched if o.get("enrich_ok"))
    n_priced = sum(1 for o in enriched if o.get("price"))
    n_imaged = sum(1 for o in enriched if o.get("image"))
    ok(f"enrich: {n_ok}/{len(enriched)} jsonld; {n_priced} price; {n_imaged} image")
    for o in enriched:
        err_mark = "X" if o.get("enrich_error") else " "
        print(
            f"  {err_mark} shop={o['shop'][:22]:22}  "
            f"price={(o.get('price') or '-'):>10}  "
            f"rating={(o.get('rating') or '-'):<5}  "
            f"img={'Y' if o.get('image') else 'N'}  "
            f"err={(o.get('enrich_error') or '')[:30]}"
        )
    save_json("05_enriched", enriched)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
