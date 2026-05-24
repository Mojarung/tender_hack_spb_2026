"""03 — Find product URLs in the Google Shopping DOM.

Cards have no <a href>. Try: data-* attributes that look like IDs, then
ld+json (sometimes Google embeds Product schema), then HTML extraction
from parent containers."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent))

import nodriver as uc

from _common import OUT_DIR, PROFILE_DIR, info, ok, query_from_argv, save_json, section

PROBE_JS = r"""
(() => {
  const out = {ld_blocks: 0, ld_products: [], data_attrs_per_card: [], all_links_with_shop_redir: []};

  // 1) Try JSON-LD
  for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
    out.ld_blocks++;
    try {
      const j = JSON.parse(node.textContent || '{}');
      const walk = (n) => {
        if (Array.isArray(n)) n.forEach(walk);
        else if (n && typeof n === 'object') {
          if (n['@type'] === 'Product' || (Array.isArray(n['@type']) && n['@type'].includes('Product'))) {
            out.ld_products.push({
              name: n.name, url: n.url, image: n.image,
              offers: n.offers, brand: n.brand,
            });
          }
          for (const v of Object.values(n)) walk(v);
        }
      };
      walk(j);
    } catch (e) {}
  }

  // 2) Get data-* attributes of first 3 cards
  const ruble = /\d[\d\s]*\s*(?:₽|руб)/i;
  const cards = [];
  for (const img of document.querySelectorAll('img')) {
    let el = img;
    for (let d = 0; d < 10 && el; d++) {
      el = el.parentElement;
      if (!el) break;
      if (ruble.test(el.innerText || '')) { cards.push(el); break; }
    }
    if (cards.length >= 3) break;
  }
  for (const c of cards) {
    const attrs = {};
    for (const a of c.attributes) attrs[a.name] = (a.value || '').slice(0, 80);
    out.data_attrs_per_card.push(attrs);
  }

  // 3) Sniff ALL anchors with /url? or /aclk? — Google redirect URLs
  for (const a of document.querySelectorAll('a[href*="/url?"], a[href*="/aclk?"], a[href*="shopping/product/"]')) {
    out.all_links_with_shop_redir.push((a.getAttribute('href') || '').slice(0, 300));
  }

  return JSON.stringify(out);
})()
"""


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"FIND URL — {query!r}")
    browser = await uc.start(
        headless=False, lang="ru-RU",
        user_data_dir=str(PROFILE_DIR.resolve()),
        browser_args=["--lang=ru-RU", "--window-size=1600,1000"],
    )
    try:
        url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=shop&hl=ru&gl=ru"
        info(f"navigating: {url}")
        tab = await browser.get(url)
        await asyncio.sleep(5)
        raw = await tab.evaluate(PROBE_JS, await_promise=False)
        data = json.loads(raw) if isinstance(raw, str) else raw
        ok(f"ld_blocks={data['ld_blocks']}  ld_products={len(data['ld_products'])}")
        ok(f"data-attrs samples = {len(data['data_attrs_per_card'])}")
        for a in data["data_attrs_per_card"]:
            print("  attrs:", list(a.keys()))
        ok(f"shop-redir links = {len(data['all_links_with_shop_redir'])}")
        for u in data["all_links_with_shop_redir"][:5]:
            print(f"  · {u[:150]}")
        save_json("03_url_probe", data)
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
