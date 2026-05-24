"""01 — WB search baseline (current prod scrapers/wb.py reproduction).

PURPOSE
    Reproduce the exact request the prod adapter makes against
    `search.wb.ru/exactmatch/ru/common/v18/search`. Confirms:
      - public JSON API still works without auth/captcha
      - rate limit hasn't tightened past ~10 RPS/IP
      - response shape (products[*]) is unchanged

    If this script gets 200 + products[], the prod L1 is healthy.
    If it 429s, we hit the new floor; if it 403s, WB started doing
    something new (TLS check, header check) — switch to 02 which
    tries curl_cffi impersonation.

USAGE
    cd wb_research
    uv run python 01_search_baseline.py "шины 205 55 R16"

EXIT CODES
    0  — 200 OK, products[] non-empty
    1  — 429 / 403 / other non-200
    2  — 200 OK but products[] empty (soft-block or no results)
    3  — network/import error
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    WB_DEFAULT_DEST,
    WB_HEADERS,
    WB_REGIONS,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v18/search"


def _params(query: str, dest: int) -> dict[str, str]:
    """Verbatim params the wildberries.ru website sends as of May 2026.
    Match exactly — Wb infra caches by full query string."""
    return {
        "ab_testid": "false",
        "appType": "1",
        "curr": "rub",
        "dest": str(dest),
        "hide_dtype": "13",
        "lang": "ru",
        "page": "1",
        "query": query,
        "regions": WB_REGIONS,
        "resultset": "catalog",
        "sort": "popular",
        "spp": "30",
        "suppressSpellcheck": "false",
    }


async def main() -> int:
    section("WB SEARCH BASELINE — search.wb.ru v18, plain httpx")

    try:
        import httpx
    except ImportError:
        err("httpx not installed — run `uv sync` in wb_research/")
        return 3

    query = query_from_argv()
    info(f"query  = {query!r}")
    info(f"dest   = {WB_DEFAULT_DEST}  (Moscow)")
    info(f"url    = {SEARCH_URL}")

    with Timer() as t:
        try:
            async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=12) as c:
                resp = await c.get(SEARCH_URL, params=_params(query, WB_DEFAULT_DEST))
        except httpx.HTTPError as exc:
            err(f"network error: {exc}")
            return 3

    info(f"status = {resp.status_code}  ({t.elapsed_ms} ms, {len(resp.content)} bytes)")
    info(f"server = {resp.headers.get('server', '-')}")

    if resp.status_code == 429:
        retry = resp.headers.get("x-ratelimit-retry") or resp.headers.get("retry-after") or "?"
        err(f"RATE LIMITED — X-Ratelimit-Retry={retry}s")
        save_json("01_429", {"status": 429, "headers": dict(resp.headers)})
        return 1
    if resp.status_code != 200:
        err(f"NOT 200 — body: {resp.text[:300]!r}")
        save_json("01_block", {"status": resp.status_code, "body_preview": resp.text[:1500]})
        return 1

    try:
        body = resp.json()
    except Exception as exc:
        err(f"non-JSON response: {exc}")
        save_json("01_nonjson", {"body_preview": resp.text[:1500]})
        return 1

    products = body.get("products") or (body.get("data") or {}).get("products") or []
    if not products:
        warn("200 OK but no products — soft block or genuinely empty result set")
        save_json("01_empty", body)
        return 2

    ok(f"got {len(products)} products")
    for i, p in enumerate(products[:5], 1):
        nm = p.get("id")
        name = (p.get("name") or "")[:60]
        sizes = p.get("sizes") or [{}]
        price = sizes[0].get("price", {}).get("total")
        info(f"  {i}. nm={nm} | {name}... | price={price}")

    path = save_json("01_ok", body)
    ok(f"saved → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
