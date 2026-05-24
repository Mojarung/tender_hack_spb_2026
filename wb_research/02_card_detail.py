"""02 — full card.json from basket-CDN.

PURPOSE
    This is the missing-link in prod: `scrapers/wb.py` only reads
    search.wb.ru which doesn't carry characteristics or description.
    The real product card with `grouped_options[]` lives at:

        https://basket-{NN}.wbbasket.ru/vol{V}/part{P}/{nm}/info/ru/card.json

    where {NN} is the basket shard for the nm_id (currently 01..35
    with new shards added every few months). We compute NN from a
    deterministic table — if the computed shard 404s, walk ±1..±5
    around it (WildberriesToolsMCP pattern; covers shards added since
    the table was frozen).

    Returns the canonical pricepulse-shaped enrichment dict:
        {imt_id, imt_name, description, brand, options[], grouped_options[],
         photo_count, has_video, subj_root_name}

USAGE
    cd wb_research
    uv run python 02_card_detail.py <nm_id>
    # nm_id is the WB product id; get it from the URL or 01_search_baseline output:
    uv run python 02_card_detail.py 147319365

EXIT CODES
    0 — got card.json + parsed options
    1 — every shard 404'd (orphan nm_id?)
    2 — got JSON but no characteristics
    3 — network/import error
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    WB_HEADERS,
    basket_for,
    card_json_url,
    err,
    info,
    ok,
    save_json,
    section,
    warn,
)


def _flatten_characteristics(card: dict) -> list[tuple[str, str, str]]:
    """Return [(group, name, value), ...]. grouped_options is preferred
    (lets the modal show section headers); falls back to flat options[]
    if missing."""
    out: list[tuple[str, str, str]] = []
    for grp in card.get("grouped_options") or []:
        gn = (grp.get("group_name") or "").strip()
        for opt in grp.get("options") or []:
            name = (opt.get("name") or "").strip()
            value = str(opt.get("value") or "").strip()
            if name and value:
                out.append((gn, name, value))
    if not out:
        for opt in card.get("options") or []:
            name = (opt.get("name") or "").strip()
            value = str(opt.get("value") or "").strip()
            if name and value:
                out.append(("", name, value))
    return out


async def _fetch_card(nm_id: int, timeout: float = 8.0) -> tuple[dict | None, str | None]:
    """Try the computed basket shard first, then walk ±1..±5. Returns
    (parsed_body, used_shard). Both None on full failure."""
    import httpx

    primary = int(basket_for(nm_id))
    candidates = [primary]
    for d in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
        n = primary + d
        if 1 <= n <= 60 and n not in candidates:
            candidates.append(n)

    async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=timeout) as c:
        for nn in candidates:
            url = card_json_url(nm_id, shard=f"{nn:02d}")
            try:
                r = await c.get(url)
            except httpx.HTTPError as exc:
                info(f"  shard={nn:02d}: network error ({exc})")
                continue
            if r.status_code == 200 and r.content:
                ok(f"  shard={nn:02d}: HIT ({len(r.content)} bytes)")
                return r.json(), f"{nn:02d}"
            info(f"  shard={nn:02d}: HTTP {r.status_code}")
    return None, None


async def main() -> int:
    section("WB CARD.JSON — full product enrichment from basket-CDN")

    if len(sys.argv) < 2:
        err("usage: uv run python 02_card_detail.py <nm_id>")
        return 3
    try:
        nm_id = int(sys.argv[1])
    except ValueError:
        err(f"nm_id must be an integer; got {sys.argv[1]!r}")
        return 3

    info(f"nm_id     = {nm_id}")
    info(f"vol       = {nm_id // 100_000}, part = {nm_id // 1_000}")
    info(f"computed shard = {basket_for(nm_id)}")
    info("trying shard cascade (computed → ±1..±5) ...")

    with Timer() as t:
        card, shard = await _fetch_card(nm_id)

    if not card:
        err(f"every basket shard returned non-200 ({t.elapsed_ms} ms)")
        err("either nm_id is wrong, or WB added new shards past the cascade — verify in browser")
        return 1

    chars = _flatten_characteristics(card)
    imt_id = card.get("imt_id")
    photo_count = (card.get("media") or {}).get("photo_count")
    has_video = (card.get("media") or {}).get("has_video")

    ok(f"shard={shard}, total = {t.elapsed_ms} ms")
    info(f"imt_id      = {imt_id}  (use this for /feedbacks/v2/)")
    info(f"imt_name    = {(card.get('imt_name') or '')[:80]!r}")
    info(f"brand       = {(card.get('selling') or {}).get('brand_name')}")
    info(f"category    = {card.get('subj_root_name')} → {card.get('subj_name')}")
    info(f"description = {(card.get('description') or '')[:120]!r}")
    info(f"photo_count = {photo_count}, has_video = {has_video}")
    info(f"characteristics ({len(chars)} pairs):")
    for group, name, value in chars[:15]:
        prefix = f"[{group[:18]}] " if group else "  "
        print(f"    {prefix}{name}: {value[:70]}")
    if len(chars) > 15:
        info(f"    …and {len(chars) - 15} more (see saved JSON)")

    path = save_json(
        "02_card_ok",
        {"nm_id": nm_id, "shard": shard, "characteristics": chars, "raw": card},
    )
    ok(f"saved → {path}")
    if not chars:
        warn("card.json present but characteristics empty — this product likely has none on WB")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
