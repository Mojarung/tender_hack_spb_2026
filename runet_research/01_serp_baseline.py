"""01 — Open Yandex SERP for a query, screenshot, dump tab links.

Goal: confirm we're not getting captcha-walled, find the "Покупки" tab.

USAGE
    uv run python 01_serp_baseline.py "iphone 15 128"
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
    err, info, ok, query_from_argv, save_json, section,
)


TABS_DUMP_JS = r"""
(() => {
  // Yandex SERP service tabs sit in the header — typically nav>ul>li>a
  // with text "Изображения", "Видео", "Карты", "Покупки", "Маркет"...
  const links = [];
  for (const a of document.querySelectorAll('a')) {
    const text = (a.innerText || a.textContent || '').trim();
    if (!text) continue;
    // Heuristic: tab links are short, contain Russian noun
    if (text.length > 30) continue;
    const href = a.getAttribute('href') || '';
    if (!href) continue;
    // Filter to likely service-tab patterns
    const isService =
      /service=/i.test(href) ||
      /^\/(images|video|maps|market|tovary|news|q)/i.test(href) ||
      /Покуп|Маркет|Изобр|Видео|Карт/i.test(text);
    if (!isService) continue;
    const r = a.getBoundingClientRect();
    links.push({
      text,
      href,
      visible: r.width > 0 && r.height > 0,
      x: Math.round(r.x), y: Math.round(r.y),
    });
  }
  // Also dump page title + any captcha indicator
  const captcha = document.querySelector('[class*=captcha i], [id*=captcha i]');
  return JSON.stringify({
    title: document.title,
    url: location.href,
    captcha_present: !!captcha,
    tabs: links.slice(0, 30),
  });
})()
"""


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"YANDEX SERP — {query!r}")

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

        # Always screenshot first — fastest way to see whether we hit captcha
        shot = OUT_DIR / "01_serp.png"
        await tab.save_screenshot(str(shot))
        ok(f"screenshot: {shot}")

        # Dump tab/link candidates
        raw = await tab.evaluate(TABS_DUMP_JS, await_promise=False)
        data = json.loads(raw) if isinstance(raw, str) else raw
        info(f"title: {data['title']!r}")
        info(f"url: {data['url']}")
        if data["captcha_present"]:
            err("captcha detected!")
        ok(f"found {len(data['tabs'])} candidate tabs")
        for t in data["tabs"]:
            print(f"  · {t['text']!r:30}  href={t['href'][:80]:80}  visible={t['visible']}")
        save_json("01_serp_tabs", data)
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
