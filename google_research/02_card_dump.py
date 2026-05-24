"""02 — Dump the actual product-card DOM to find selectors.

For each [role=listitem] in Google Shopping, print:
  - outerHTML (truncated)
  - immediate children classes
  - what fields we can find: name, price, image, seller, rating, url
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent))

import nodriver as uc

from _common import OUT_DIR, PROFILE_DIR, info, ok, query_from_argv, save_json, section

DUMP_JS = r"""
(() => {
  // Find elements that look like product tiles: image + ruble price.
  // Walk from each <img> upward to the smallest enclosing block that
  // also contains a price string. That's our "card" envelope.
  const ruble = /\d[\d\s]*\s*(?:₽|руб)/i;
  const seen = new Set();
  const cards = [];
  for (const img of document.querySelectorAll('img')) {
    const w = img.naturalWidth || img.width || 0;
    if (w && w < 80) continue;     // skip icons / favicons
    let el = img;
    for (let depth = 0; depth < 10 && el; depth++) {
      el = el.parentElement;
      if (!el) break;
      const txt = el.innerText || '';
      if (!ruble.test(txt)) continue;
      if (txt.length > 1500) break;    // walked too high
      if (seen.has(el)) break;
      seen.add(el);
      cards.push(el);
      break;
    }
  }
  const out = [];
  for (const c of cards.slice(0, 5)) {
    const links = Array.from(c.querySelectorAll('a[href]')).map(a => a.getAttribute('href').slice(0, 200));
    out.push({
      tag: c.tagName,
      cls: (c.className||'').toString().slice(0, 200),
      text: (c.innerText || '').slice(0, 500),
      links: links.slice(0, 6),
      outerHTML: c.outerHTML.slice(0, 3000),
    });
  }
  return JSON.stringify({card_count: cards.length, sample: out});
})()
"""


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"GOOGLE SHOPPING CARD DUMP — {query!r}")

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
        raw = await tab.evaluate(DUMP_JS, await_promise=False)
        data = json.loads(raw) if isinstance(raw, str) else raw
        ok(f"cards found={data['card_count']}")
        save_json("02_card_dump", data)
        ok(f"saved {len(data['sample'])} sample cards")
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
