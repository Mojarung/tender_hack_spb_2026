"""03 — Extract product offers directly from Yandex SERP organic results.

Output shape matches ProductOffer-ish (name/price/url/image/rating/
reviews_count/shop). Save to JSON for inspection.

USAGE
    uv run python 03_serp_extractor.py "iphone 15"
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
    OUT_DIR, PROFILE_DIR,
    info, ok, query_from_argv, save_json, section, warn,
)


# Extraction JS — runs in the SERP DOM, returns JSON array of offer-shaped objects.
EXTRACTOR_JS = r"""
(() => {
  const items = Array.from(document.querySelectorAll('li.serp-item, .serp-item'));
  const out = [];
  for (const it of items) {
    // Title + link — first <h2>/<h3> inside .OrganicTitle
    const titleEl = it.querySelector('.OrganicTitle h2, .OrganicTitle, h2, h3');
    const linkEl  = it.querySelector('.OrganicTitle-Link, a.Link[href], a[href]');
    if (!titleEl || !linkEl) continue;

    const title = (titleEl.innerText || titleEl.textContent || '').trim();
    if (!title || title.length < 3) continue;

    let href = linkEl.getAttribute('href') || '';
    if (!href) continue;
    // Yandex wraps outbound links in yabs.yandex.ru/count/... — that's
    // a tracker. The REAL target is in a sibling .Path/.Link__path element.
    if (/yabs\.yandex\.ru/i.test(href)) {
      const pathEl = it.querySelector('.Path .Link, .Path b, .OrganicSubtitle-Path, .Path');
      if (pathEl) {
        const realPath = (pathEl.innerText || '').trim();
        if (realPath && /^https?:\/\//i.test(realPath)) {
          href = realPath;
        } else if (realPath) {
          // Often shown as "shop.ru ›path1 ›path2" — pick the host token
          const host = realPath.split(/[›·•·]/)[0].trim();
          if (host) href = "https://" + host;
        }
      }
    }

    // Skip non-product Yandex services
    if (/^https?:\/\/(yandex\.ru|ya\.ru)\//i.test(href)) continue;

    // Price: any element with "price" in class / data-price attribute
    let price = null;
    const priceEl = it.querySelector('[class*=price i], [data-price], .OrganicCarousel .Title');
    if (priceEl) {
      const text = (priceEl.innerText || '').trim();
      // Pattern: 19 990 ₽  / 19990  / 19 990 руб
      const m = text.match(/(\d[\d\s ]*)\s*(?:₽|руб|р\.|RUB)/i);
      if (m) price = m[1].replace(/[\s ]/g, '');
    }

    // Rating + reviews count
    let rating = null;
    let reviews_count = null;
    const ratingEl = it.querySelector('[class*=rating i], [class*=Rating], [class*=stars i]');
    if (ratingEl) {
      const text = (ratingEl.innerText || '').replace(/\s+/g, ' ').trim();
      const rm = text.match(/(\d[.,]\d)\s*(?:из\s*5)?/);
      if (rm) rating = parseFloat(rm[1].replace(',', '.'));
      const cm = text.match(/(\d+(?:[.,]\d+)?)\s*([KkКк])?\s*(?:отзыв|оценк|review)/i);
      if (cm) {
        let n = parseFloat(cm[1].replace(',', '.'));
        if (cm[2]) n *= 1000;
        reviews_count = Math.round(n);
      }
    }

    // Image — Yandex shows shop thumbnail next to e-commerce results
    let image = null;
    const imgEl = it.querySelector('img[src]');
    if (imgEl) {
      const src = imgEl.getAttribute('src') || '';
      if (src && !/yastatic\.net|favicon|placeholder/i.test(src)) image = src;
    }

    const has_cart = !!it.querySelector('[class*=cart i], [class*=shop i], [class*=basket i]');

    // Only keep items that look like products (cart icon OR price OR rating)
    if (!has_cart && !price && !rating) continue;

    out.push({
      title, url: href, price, rating, reviews_count, image, has_cart,
    });
  }
  return JSON.stringify(out);
})()
"""


def extract_shop(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return re.sub(r"^www\.", "", host)
    except Exception:
        return ""


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"YANDEX SERP EXTRACTOR — {query!r}")

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
        if not offers:
            warn("0 offers extracted — captcha or SERP layout changed")
            return 1
        for o in offers:
            o["shop"] = extract_shop(o["url"])
        ok(f"extracted {len(offers)} offers")
        # Console-safe summary (no cyrillic, no ₽) — full data in JSON
        for o in offers[:10]:
            print(
                f"  · shop={o['shop'][:25]:25}  "
                f"price={(o.get('price') or '-'):>8}  "
                f"rating={o.get('rating') or '-':<4}  "
                f"reviews={o.get('reviews_count') or '-'}  "
                f"img={'Y' if o.get('image') else 'N'}"
            )
        save_json("03_offers", offers)
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
