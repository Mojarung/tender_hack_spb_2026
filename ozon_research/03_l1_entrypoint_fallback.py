"""03 — entrypoint-api.bx fallback.

PURPOSE
    Ozon mirrors composer-api at a second host:
        https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2
    Same `widgetStates` shape, DIFFERENT rate-limit pool. JTJag's
    README confirms it. So when composer-api 403s, try entrypoint-api
    same-payload; one of them is usually open.

    This script runs both back-to-back and tells you which one Ozon is
    blocking right now from your IP.

USAGE
    cd ozon_research
    uv run python 03_l1_entrypoint_fallback.py "шины 205 55 R16"

EXIT CODES
    0  — at least one of the two hosts returned 200 + offers
    1  — both blocked or empty (try 09 Patchright L2)
    3  — network/import error
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    android_cookies,
    android_headers,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

HOSTS = [
    ("composer-api", "https://api.ozon.ru/composer-api.bx/page/json/v2", "api.ozon.ru"),
    ("entrypoint-api", "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2", "www.ozon.ru"),
]


async def _fetch(label: str, base: str, host_header: str, query: str) -> tuple[int, dict | None, int]:
    """Returns (status, parsed_body_or_none, elapsed_ms)."""
    from curl_cffi.requests import AsyncSession
    import orjson

    path = f"/search/?text={quote(query)}&from_global=true"
    url = f"{base}?url={quote(path, safe='')}"
    headers = android_headers(extra={"Host": host_header})
    cookies = android_cookies()

    info(f"--- {label} ---")
    info(f"url    = {url}")

    with Timer() as t:
        try:
            async with AsyncSession(impersonate="chrome131_android", timeout=15) as s:
                for k, v in cookies.items():
                    s.cookies.set(k, v)
                resp = await s.get(url, headers=headers)
        except Exception as exc:
            err(f"{label}: network error: {exc}")
            return 0, None, t.elapsed_ms

    info(f"status = {resp.status_code} ({t.elapsed_ms} ms)")
    if resp.status_code != 200:
        warn(f"{label}: blocked (HTTP {resp.status_code})")
        save_json(f"03_{label}_block", {"status": resp.status_code, "body_preview": resp.text[:1500]})
        return resp.status_code, None, t.elapsed_ms

    try:
        body = orjson.loads(resp.content)
    except orjson.JSONDecodeError:
        warn(f"{label}: 200 but non-JSON")
        save_json(f"03_{label}_nonjson", {"body_preview": resp.text[:1500]})
        return resp.status_code, None, t.elapsed_ms

    keys = list((body.get("widgetStates") or {}).keys())
    n_search = sum(1 for k in keys if k.startswith(("searchResultsV2", "tileGridDesktop", "skuList")))
    ok(f"{label}: 200, {n_search} search widget(s), {len(keys)} total widgets")
    return resp.status_code, body, t.elapsed_ms


async def main() -> int:
    section("ENTRYPOINT-API FALLBACK — race composer-api vs entrypoint-api")

    try:
        import curl_cffi  # noqa: F401
        import orjson  # noqa: F401
    except ImportError as exc:
        err(f"missing dep: {exc}")
        return 3

    query = query_from_argv()
    info(f"query = {query!r}\n")

    results: dict[str, dict | None] = {}
    for label, base, host in HOSTS:
        status, body, ms = await _fetch(label, base, host, query)
        results[label] = body

    section("Summary")
    winners = [lbl for lbl, body in results.items() if body and (body.get("widgetStates") or {})]
    if not winners:
        err("Both hosts blocked. Try 09_patchright_l2.py or wait/rotate IP.")
        return 1

    ok(f"Winning host(s): {winners}")
    save_json("03_entrypoint_summary", {lbl: bool(b) for lbl, b in results.items()})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
