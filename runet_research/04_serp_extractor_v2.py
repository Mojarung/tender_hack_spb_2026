"""04 — SERP extractor v2: fix price regex, dedup, skip ya.ru hosts.

Improvements over 03:
- Price regex grabs the FIRST number-with-currency, not concatenated runs.
- Skip ya.ru / market.yandex.ru / yandex.ru hosts (those are separate
  sources or non-product Yandex pages).
- Dedup by canonical (shop, title) key — keeps first occurrence.
- Mark `image=null` when the only image is a yapic shop avatar (we want
  product photos; the UI will fallback to a placeholder otherwise).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).parent))

import nodriver as uc

from _common import (
    PROFILE_DIR, info, ok, query_from_argv, save_json, section, warn,
)


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
      // Tracker — try to recover real URL from the path span
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

    // Price — FIRST money-token only, not greedy concat
    let price = null;
    const priceEl = it.querySelector('[class*=price i], [data-price]');
    if (priceEl) {
      const text = (priceEl.innerText || '').replace(/ /g, ' ');
      // Match a digit run with optional thousand-separators, followed
      // by RUB-ish currency. Non-greedy.
      const m = text.match(/(\d{1,3}(?:[ \s]\d{3})*|\d+)\s*(?:₽|руб|р\.|RUB)/i);
      if (m) price = m[1].replace(/\s/g, '');
    }

    let rating = null, reviews_count = null;
    const ratingEl = it.querySelector('[class*=rating i], [class*=Rating], [class*=stars i]');
    if (ratingEl) {
      const text = (ratingEl.innerText || '').replace(/\s+/g, ' ').trim();
      // Match "4,5", "4.5", or plain "4" / "5" (when no decimal shown)
      const rm = text.match(/(\d(?:[.,]\d)?)\s*(?:из\s*5)?/);
      if (rm) rating = parseFloat(rm[1].replace(',', '.'));
      const cm = text.match(/(\d+(?:[.,]\d+)?)\s*([KkКк])?\s*(?:отзыв|оценк|review)/i);
      if (cm) {
        let n = parseFloat(cm[1].replace(',', '.'));
        if (cm[2]) n *= 1000;
        reviews_count = Math.round(n);
      }
    }

    // Image — prefer real product thumb, NOT shop avatar (yapic/islands).
    let image = null;
    for (const im of it.querySelectorAll('img[src]')) {
      const src = im.getAttribute('src') || '';
      if (!src) continue;
      if (/yastatic|favicon|placeholder/i.test(src)) continue;
      // yapic avatars are shop logos — keep looking
      if (/avatars\.mds\.yandex\.net.*yapic/i.test(src)) continue;
      image = src;
      break;
    }

    const has_cart = !!it.querySelector('[class*=cart i], [class*=shop i], [class*=basket i]');
    if (!has_cart && !price && !rating) continue;

    out.push({title, url: href, price, rating, reviews_count, image, has_cart});
  }
  return JSON.stringify(out);
})()
"""


# Hosts to drop entirely — Yandex services + the marketplaces we scrape
# directly through their own adapters (Runet must not duplicate them).
SKIP_HOST_PATTERNS = (
    re.compile(r"(^|\.)yandex\.ru$"),
    re.compile(r"(^|\.)ya\.ru$"),
    re.compile(r"market\.yandex\.ru$"),
    re.compile(r"(^|\.)wildberries\.ru$"),
    re.compile(r"(^|\.)ozon\.ru$"),
)


def shop_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return re.sub(r"^www\.", "", host)


def should_skip(shop: str) -> bool:
    return any(p.search(shop) for p in SKIP_HOST_PATTERNS)


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"YANDEX SERP EXTRACTOR v2 — {query!r}")

    browser = await uc.start(
        headless=False, lang="ru-RU",
        user_data_dir=str(PROFILE_DIR.resolve()),
        browser_args=["--lang=ru-RU", "--window-size=1600,1000"],
    )
    try:
        url = f"https://yandex.ru/search/?text={quote_plus(query)}"
        info(f"navigating: {url}")
        tab = await browser.get(url)
        await asyncio.sleep(5)
        raw = await tab.evaluate(EXTRACTOR_JS, await_promise=False)
        offers = json.loads(raw) if isinstance(raw, str) else raw

        # Post-process: shop extraction + skip yandex hosts + dedup
        seen: set[tuple[str, str]] = set()
        clean: list[dict] = []
        for o in offers:
            shop = shop_of(o["url"])
            if not shop or should_skip(shop):
                continue
            key = (shop, o["title"][:60].lower())
            if key in seen:
                continue
            seen.add(key)
            o["shop"] = shop
            clean.append(o)

        if not clean:
            warn("0 offers after dedup/skip")
            return 1

        ok(f"raw={len(offers)}  clean={len(clean)}")
        for o in clean[:12]:
            print(
                f"  · shop={o['shop'][:22]:22}  "
                f"price={(o.get('price') or '-'):>8}  "
                f"rating={o.get('rating') or '-':<4}  "
                f"reviews={o.get('reviews_count') or '-':<8}  "
                f"img={'Y' if o.get('image') else 'N'}"
            )
        save_json("04_offers_clean", clean)
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
