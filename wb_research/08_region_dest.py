"""08 — region (dest) parameter — does it actually change prices/stock?

PURPOSE
    `scrapers/wb.py` hardcodes `dest=-1257786` (Moscow). The full
    Yandex `region_id` (lr) → WB `dest` mapping isn't public; we have
    12 verified values in `_common.YANDEX_LR_TO_WB_DEST`. This script
    runs the same query against ALL 12 dest codes and reports:
      - price diffs per region (max-min spread)
      - stock count per region
      - which regions return identical data (cache hit indicator)

    Demonstrates whether wiring region_id properly is worth doing for
    the demo (spoiler: depends heavily on the SKU).

USAGE
    cd wb_research
    uv run python 08_region_dest.py "iphone 15 128"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    WB_HEADERS,
    WB_REGIONS,
    YANDEX_LR_TO_WB_DEST,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
)

SEARCH = "https://search.wb.ru/exactmatch/ru/common/v18/search"


CITY_NAMES = {
    213: "Москва", 2: "СПб", 54: "Екатеринбург", 65: "Новосибирск",
    35: "Краснодар", 43: "Казань", 47: "Нижний Новгород",
    39: "Ростов-на-Дону", 172: "Уфа", 51: "Самара",
    193: "Воронеж", 50: "Пермь",
}


def _params(query: str, dest: int) -> dict[str, str]:
    return {
        "ab_testid": "false", "appType": "1", "curr": "rub",
        "dest": str(dest), "hide_dtype": "13", "lang": "ru",
        "page": "1", "query": query, "regions": WB_REGIONS,
        "resultset": "catalog", "sort": "popular", "spp": "30",
        "suppressSpellcheck": "false",
    }


async def _hit(client, query: str, dest: int) -> dict:
    with Timer() as t:
        try:
            r = await client.get(SEARCH, params=_params(query, dest))
        except Exception as exc:
            return {"dest": dest, "error": str(exc), "elapsed_ms": t.elapsed_ms}
    if r.status_code != 200:
        return {"dest": dest, "http": r.status_code, "elapsed_ms": t.elapsed_ms}
    try:
        body = r.json()
    except Exception:
        return {"dest": dest, "error": "non-json", "elapsed_ms": t.elapsed_ms}
    products = body.get("products") or (body.get("data") or {}).get("products") or []
    if not products:
        return {"dest": dest, "elapsed_ms": t.elapsed_ms, "count": 0}
    p0 = products[0]
    sizes = p0.get("sizes") or [{}]
    price_top = sizes[0].get("price", {}).get("total")
    stocks_top = sum((s.get("qty") or 0) for sz in sizes for s in (sz.get("stocks") or []))
    # Sample 3 prices to detect regional pricing
    prices_3 = [
        ((p.get("sizes") or [{}])[0].get("price", {}) or {}).get("total")
        for p in products[:3]
    ]
    return {
        "dest": dest,
        "elapsed_ms": t.elapsed_ms,
        "count": len(products),
        "top_nm": p0.get("id"),
        "top_price_kop": price_top,
        "top_stock_qty": stocks_top,
        "top3_prices_kop": prices_3,
    }


async def main() -> int:
    section("WB REGION/DEST PROBE — same query across 12 cities")

    try:
        import httpx
    except ImportError:
        err("httpx not installed")
        return 3

    query = query_from_argv()
    info(f"query = {query!r}")

    rows: list[dict] = []
    async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=10) as c:
        # Sequential to be polite (12 hits at once would burn rate budget).
        for lr, dest in YANDEX_LR_TO_WB_DEST.items():
            r = await _hit(c, query, dest)
            r["lr"] = lr
            r["city"] = CITY_NAMES.get(lr, "-")
            rows.append(r)
            price_rub = (r.get("top_price_kop") or 0) / 100
            print(f"  {r['city']:<22} lr={lr:>4} dest={dest:>9}  "
                  f"count={r.get('count', 0):>3}  "
                  f"top {r.get('top_nm','-')}: {price_rub:>9.0f}₽  "
                  f"stock={r.get('top_stock_qty', 0):>3}  ({r['elapsed_ms']} ms)")
            await asyncio.sleep(0.4)

    section("Summary")
    prices = [r["top_price_kop"] for r in rows if r.get("top_price_kop")]
    if len(prices) >= 2:
        spread = (max(prices) - min(prices)) / 100
        if spread > 0:
            ok(f"top SKU price spread across cities: {spread:.2f} ₽ "
               f"(min={min(prices)/100:.0f}, max={max(prices)/100:.0f})")
        else:
            info("identical price across all 12 cities — region likely doesn't affect this SKU")

    save_json("08_region_dest", {"query": query, "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
