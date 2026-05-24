"""06 — Sniff CDP Network during a Google Shopping search to harvest
the per-card /aclk? redirect URLs that Google pre-emits even without
a click (performance hint for hover-prediction).

If we capture them in order, we can match aclk[i] → card[i] without
any synthetic-click trickery at all.
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


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv("iphone 15 купить")
    section(f"NETWORK SNIFF — {query!r}")
    browser = await uc.start(
        headless=False, lang="ru-RU",
        user_data_dir=str(PROFILE_DIR.resolve()),
        browser_args=["--lang=ru-RU", "--window-size=1600,1000"],
    )
    captured: list[dict] = []
    try:
        tab = browser.main_tab
        # nodriver handler shape: (event) -> None or coroutine
        async def on_request(event):
            try:
                url = event.request.url
            except Exception:
                return
            # Filter out static assets / pure CDN — keep XHR/Fetch/Doc/aclk
            if any(tok in url for tok in (
                ".css", ".woff", ".png", ".jpg", ".webp", ".svg",
                "gstatic.com/shopping?", "/maps/", "/static/",
            )):
                return
            rt = str(getattr(event, "type_", getattr(event, "resource_type", "")))
            captured.append({
                "url": url[:500],
                "type": rt,
                "method": event.request.method,
            })
        try:
            tab.add_handler(uc.cdp.network.RequestWillBeSent, on_request)
        except Exception as exc:
            info(f"add_handler failed: {exc} — trying alternative")
        await tab.send(uc.cdp.network.enable())

        url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=shop&hl=ru&gl=ru"
        info(f"navigating: {url}")
        await tab.get(url)
        await asyncio.sleep(8)    # let lazy-loaded resources settle

        # Scroll to trigger any hover-preloads
        await tab.evaluate("window.scrollTo(0, 1500)", await_promise=False)
        await asyncio.sleep(2)
        await tab.evaluate("window.scrollTo(0, 0)", await_promise=False)
        await asyncio.sleep(2)

        # Group by host to see the shape of traffic
        from collections import Counter
        from urllib.parse import urlparse
        hosts = Counter(urlparse(c["url"]).hostname or "" for c in captured)
        ok(f"captured {len(captured)} non-static requests across {len(hosts)} hosts")
        for host, n in hosts.most_common(10):
            print(f"  {n:4} {host}")
        # Sample interesting ones — anything with shopping/product/ or aclk
        print("\nshopping / aclk / merchant samples:")
        keys = ("shopping/product", "/aclk", "googleadservices", "doubleclick",
                "/url?", "/shopping/", "/onelink/", "/click")
        seen = 0
        for c in captured:
            if any(k in c["url"] for k in keys):
                safe = c["url"].encode("ascii", errors="replace").decode("ascii")
                print(f"  {safe[:160]}")
                seen += 1
                if seen >= 15:
                    break
        save_json("06_network", captured)
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
