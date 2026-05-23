"""01 — L1 baseline (current scrapers/ozon.py reproduction).

PURPOSE
    Reproduce EXACTLY what production code does today, so we have a
    before/after baseline. If this script gets 200, the production
    pipeline still works. If it 403s, we know the current header set
    has decayed and the hardened script (02) should be tried.

NO PROXIES. NO RETRIES. NO COOKIES. Mirrors backend/src/pricepulse/scrapers/ozon.py.

USAGE
    cd ozon_research
    uv run python 01_l1_baseline.py "ноутбук lenovo"

EXIT CODES
    0  — 200 OK, JSON parsed, widgetStates present
    1  — non-200 HTTP
    2  — 200 OK but no widgetStates (anti-bot served a stub)
    3  — network/import error
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

# Allow running from anywhere — `uv run python 01_l1_baseline.py` etc.
sys.path.insert(0, str(Path(__file__).parent))

from _common import APP_BUILD, APP_VERSION, Timer, err, info, ok, query_from_argv, save_json, section, warn

BASE = "https://api.ozon.ru/composer-api.bx/page/json/v2"

# Exact header set as of production code on this branch.
HEADERS_NOW = {
    "User-Agent": f"ozonapp_android/{APP_VERSION}+{APP_BUILD}",
    "x-o3-app-name": "ozonapp_android",
    "x-o3-app-version": APP_VERSION,
    "x-o3-device-type": "mobile",
    "Accept": "application/json; charset=utf-8",
    "Accept-Language": "ru",
    "Host": "api.ozon.ru",
}


async def main() -> int:
    section("L1 BASELINE — current scrapers/ozon.py header set")

    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        err("curl_cffi not installed — `uv add curl_cffi` first")
        return 3

    import orjson

    query = query_from_argv()
    info(f"query  = {query!r}")
    path = f"/search/?text={quote(query)}&from_global=true"
    url = f"{BASE}?url={quote(path, safe='')}"
    info(f"url    = {url}")
    info(f"tls    = impersonate=chrome131 (current production value)")
    info(f"hdrs   = {len(HEADERS_NOW)} headers (no abt_data, no x-o3-fp, no MOBILE-GAID)")

    with Timer() as t:
        try:
            async with AsyncSession(impersonate="chrome131", timeout=15) as s:
                resp = await s.get(url, headers=HEADERS_NOW)
        except Exception as exc:
            err(f"network error: {exc}")
            return 3

    info(f"time   = {t.elapsed_ms} ms")
    info(f"status = {resp.status_code}")
    if resp.status_code != 200:
        err(f"NOT 200 — production L1 would now escalate to L2. Body bytes: {len(resp.content)}")
        save_json("01_baseline_block", {"status": resp.status_code, "body_preview": resp.text[:2000]})
        return 1

    try:
        body = orjson.loads(resp.content)
    except orjson.JSONDecodeError:
        err("200 but non-JSON — anti-bot stub HTML")
        save_json("01_baseline_nonjson", {"body_preview": resp.text[:2000]})
        return 2

    widget_states = body.get("widgetStates") or {}
    search_keys = [k for k in widget_states if k.startswith(("searchResultsV2", "tileGridDesktop", "skuList"))]
    if not search_keys:
        warn("200 + JSON but no search-result widgets — Ozon served a 'soft block' shell")
        save_json("01_baseline_soft_block", body)
        return 2

    ok(f"baseline still works — widget keys: {search_keys[:3]}{'…' if len(search_keys) > 3 else ''}")
    path = save_json("01_baseline_ok", body)
    ok(f"saved → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
