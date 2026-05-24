"""01 — Open Google Shopping for a query, screenshot, dump page title.

Verify we're not getting a captcha-wall. URL format:
    https://www.google.com/search?q=<q>&tbm=shop&hl=ru&gl=ru

Note tbm=shop puts us on the Shopping vertical directly — no need to
click a tab.

USAGE
    uv run python 01_shopping_baseline.py "iphone 15 купить"
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent))

import nodriver as uc

from _common import OUT_DIR, PROFILE_DIR, err, info, ok, query_from_argv, save_json, section

PAGE_PROBE_JS = r"""
(() => {
  // Captcha indicator + page title
  const captcha = document.querySelector('form#captcha-form, #recaptcha, [class*=captcha i]');
  // Shopping cards are typically <div jscontroller> tiles. Don't filter
  // yet — just count them for a sanity check.
  const candidateContainers = [
    'div.sh-dlr__list-result',           // older layout
    'div[jscontroller]',                 // generic SPA tile
    'a[href^="/shopping/product/"]',     // product detail link
    '[role=listitem]',
  ];
  const counts = {};
  for (const sel of candidateContainers) {
    counts[sel] = document.querySelectorAll(sel).length;
  }
  return JSON.stringify({
    title: document.title,
    url: location.href,
    captcha_present: !!captcha,
    counts,
  });
})()
"""


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"GOOGLE SHOPPING BASELINE — {query!r}")

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

        await tab.save_screenshot(str(OUT_DIR / "01_shopping.png"))
        ok("screenshot saved")

        raw = await tab.evaluate(PAGE_PROBE_JS, await_promise=False)
        data = json.loads(raw) if isinstance(raw, str) else raw
        info(f"title: {data['title']!r}")
        info(f"url: {data['url']}")
        if data["captcha_present"]:
            err("CAPTCHA wall detected")
        ok("container counts:")
        for sel, cnt in data["counts"].items():
            print(f"  {sel!r:55}  {cnt}")
        save_json("01_baseline", data)
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
