"""02 — Click "Все" tab, dump dropdown contents to find "Покупки" tab.

Yandex hides service tabs behind a "Все" dropdown when there's no room
in the main bar. Probe that dropdown to find the shopping URL.

Also probe whether SERP organic results carry e-commerce rich snippets
(price / rating / shop logo) — if yes we may skip the shopping tab and
just parse from SERP directly.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent))

import nodriver as uc

from _common import (
    OUT_DIR, PROFILE_DIR,
    info, ok, query_from_argv, save_json, section,
)


CLICK_ALL_JS = r"""
(() => {
  // The "Все" tab is a button with text === "Все"
  for (const el of document.querySelectorAll('a, button, [role=button]')) {
    const t = (el.innerText || '').trim();
    if (t === 'Все' || t === 'Всё') {
      el.click();
      return JSON.stringify({clicked: true, tag: el.tagName});
    }
  }
  return JSON.stringify({clicked: false});
})()
"""

DUMP_TAB_LIST_JS = r"""
(() => {
  // Anything that newly appeared as a tab-like link
  const all = Array.from(document.querySelectorAll('a, [role=menuitem]'));
  const out = [];
  for (const a of all) {
    const text = (a.innerText || a.textContent || '').trim();
    const href = a.getAttribute('href') || '';
    if (!text || text.length > 30 || !href) continue;
    // Filter to ru/yandex service URLs
    if (!/yandex|^\//i.test(href)) continue;
    out.push({text, href: href.slice(0, 200)});
  }
  return JSON.stringify(out.slice(0, 60));
})()
"""

# Probe organic results for inline product data (price, rating, shop)
DUMP_ORGANIC_JS = r"""
(() => {
  // SERP results are typically <li class="serp-item"> with rich data
  const items = Array.from(document.querySelectorAll('li.serp-item, .serp-item, article'));
  const out = [];
  for (const it of items.slice(0, 8)) {
    const title = it.querySelector('h2, h3, [class*=title i]');
    const link = it.querySelector('a[href]');
    const price = it.querySelector('[class*=price i], [data-price], [class*=Price]');
    const rating = it.querySelector('[class*=rating i], [class*=stars i], [class*=score i]');
    const cart = it.querySelector('[class*=cart i], [class*=shop i], [class*=basket i]');
    out.push({
      title: (title ? title.innerText.trim() : '').slice(0, 100),
      href: link ? link.getAttribute('href').slice(0, 120) : '',
      price: price ? price.innerText.trim().slice(0, 40) : null,
      rating: rating ? rating.innerText.trim().slice(0, 30) : null,
      has_cart_icon: !!cart,
      // Sample structure
      html_snip: it.outerHTML.slice(0, 300),
    });
  }
  return JSON.stringify(out);
})()
"""


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"YANDEX ALL-TAB + ORGANIC PROBE — {query!r}")

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

        # ─── Part A: organic SERP results — do they carry product data? ───
        organic_raw = await tab.evaluate(DUMP_ORGANIC_JS, await_promise=False)
        organic = json.loads(organic_raw) if isinstance(organic_raw, str) else organic_raw
        save_json("02_organic_results", organic)
        ok(f"organic results sample: {len(organic)} items")
        for o in organic[:5]:
            cart = "🛒" if o.get("has_cart_icon") else "  "
            print(f"  {cart} {o['title'][:50]:50}  price={o.get('price') or '-'}  rating={o.get('rating') or '-'}")

        # ─── Part B: click "Все" tab, dump dropdown ─────────────────────
        click_res = await tab.evaluate(CLICK_ALL_JS, await_promise=False)
        info(f"click Все: {click_res}")
        await asyncio.sleep(2)
        await tab.save_screenshot(str(OUT_DIR / "02_after_all_click.png"))
        tabs_raw = await tab.evaluate(DUMP_TAB_LIST_JS, await_promise=False)
        tabs = json.loads(tabs_raw) if isinstance(tabs_raw, str) else tabs_raw
        save_json("02_all_tabs", tabs)
        ok(f"all tab options: {len(tabs)}")
        for t in tabs:
            print(f"  · {t['text']:30}  {t['href'][:100]}")
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
