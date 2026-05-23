"""03 — imt_id resolution (search → nm_id → imt_id).

PURPOSE
    Reviews live under `imt_id`, not `nm_id`. The `imt_id` groups
    all color/size variants of a product; one imt_id can have many
    nm_ids. Three ways to get it:

      A) `root` field in search.wb.ru response  → cheapest (no extra call)
      B) `imt_id` field in card.json (02)       → 1 basket-CDN call
      C) GET https://card.wb.ru/cards/v2/detail?nm=...   → 1 card.wb.ru call

    This script tries all three on the first SKU of a search result
    so we can confirm they all return the same number and pick the
    cheapest for production.

USAGE
    cd wb_research
    uv run python 03_imt_resolution.py "ноутбук lenovo"
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
    basket_for,
    card_json_url,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

SEARCH = "https://search.wb.ru/exactmatch/ru/common/v18/search"
CARD_V2 = "https://card.wb.ru/cards/v2/detail"


def _search_params(query: str) -> dict[str, str]:
    return {
        "ab_testid": "false", "appType": "1", "curr": "rub",
        "dest": str(WB_DEFAULT_DEST), "hide_dtype": "13", "lang": "ru",
        "page": "1", "query": query, "regions": WB_REGIONS,
        "resultset": "catalog", "sort": "popular", "spp": "30",
        "suppressSpellcheck": "false",
    }


def _card_v2_params(nm_id: int) -> dict[str, str]:
    return {
        "appType": "1", "curr": "rub", "dest": str(WB_DEFAULT_DEST),
        "regions": WB_REGIONS, "spp": "27", "nm": str(nm_id),
    }


async def main() -> int:
    section("WB IMT-ID RESOLUTION — search.root vs card.json vs card.wb.ru")

    try:
        import httpx
    except ImportError:
        err("httpx not installed")
        return 3

    query = query_from_argv()
    info(f"query = {query!r}")

    async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=12) as c:
        # 1) search → first nm, root
        with Timer() as t:
            try:
                r = await c.get(SEARCH, params=_search_params(query))
            except httpx.HTTPError as exc:
                err(f"search failed: {exc}")
                return 1
        if r.status_code != 200:
            err(f"search HTTP {r.status_code}")
            return 1
        products = (r.json().get("products") or
                    (r.json().get("data") or {}).get("products") or [])
        if not products:
            warn("search returned 0 products")
            return 2
        p0 = products[0]
        nm_id = p0.get("id")
        root_imt = p0.get("root")
        info(f"search    [{t.elapsed_ms} ms]: nm={nm_id}, root={root_imt}")

        # 2) basket card.json
        with Timer() as t2:
            try:
                r2 = await c.get(card_json_url(int(nm_id)))
            except httpx.HTTPError as exc:
                err(f"  basket failed: {exc}")
                r2 = None
        card_imt = None
        if r2 is not None and r2.status_code == 200:
            try:
                card_imt = (r2.json()).get("imt_id")
            except Exception:
                pass
        info(f"basket    [{t2.elapsed_ms} ms]: imt_id={card_imt}  shard={basket_for(int(nm_id))}")

        # 3) card.wb.ru/v2
        with Timer() as t3:
            try:
                r3 = await c.get(CARD_V2, params=_card_v2_params(int(nm_id)))
            except httpx.HTTPError as exc:
                err(f"  card.wb.ru failed: {exc}")
                r3 = None
        cwb_imt = None
        if r3 is not None and r3.status_code == 200:
            try:
                d3 = r3.json()
                ps3 = d3.get("products") or (d3.get("data") or {}).get("products") or []
                if ps3:
                    cwb_imt = ps3[0].get("root")
            except Exception:
                pass
        info(f"card.v2   [{t3.elapsed_ms} ms]: root={cwb_imt}")

    section("Verdict")
    ids = {root_imt, card_imt, cwb_imt} - {None}
    if len(ids) == 1:
        ok(f"all three paths agree → imt_id={ids.pop()}")
    elif len(ids) > 1:
        warn(f"paths disagree: {ids}")
    else:
        err("no path returned an imt_id")
        return 1
    info("CHEAPEST IS A: read `root` straight out of search response — no extra call.")
    save_json("03_imt", {
        "nm_id": nm_id, "search_root": root_imt,
        "basket_imt": card_imt, "card_v2_root": cwb_imt,
    })
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
