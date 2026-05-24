"""05 — full image gallery from basket-CDN.

PURPOSE
    Prod uses one cover image (`wb_basket.image_url(nm)`). Real
    products have 1..30 photos. The full gallery URL pattern is:

        https://basket-{NN}.wbbasket.ru/vol{V}/part{P}/{nm}/images/{size}/{idx}.webp

    Sizes:
        big       — ~900px max edge (modal carousel)
        c516x688  — large preview
        c246x328  — small preview
        square    — square crop
        tm        — tiny thumbnail (placeholder)

    `photo_count` lives in card.json → `media.photo_count`. We pull
    it from card.json, then HEAD-check every `images/big/{i}.webp`
    URL to verify count is accurate (sometimes off-by-one).

USAGE
    cd wb_research
    uv run python 05_gallery.py <nm_id>
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
    image_url,
    info,
    ok,
    save_json,
    section,
    warn,
)


async def _resolve_shard(client, nm_id: int) -> str | None:
    """Same ±5 cascade as 02 — we need the actual shard, not the
    extrapolated guess, before we can build image URLs."""
    primary = int(basket_for(nm_id))
    for delta in (0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
        nn = primary + delta
        if not (1 <= nn <= 60):
            continue
        url = card_json_url(nm_id, shard=f"{nn:02d}")
        try:
            r = await client.head(url, follow_redirects=True)
        except Exception:
            continue
        if r.status_code == 200:
            return f"{nn:02d}"
    return None


async def main() -> int:
    section("WB GALLERY — full image set from basket-CDN")

    if len(sys.argv) < 2:
        err("usage: uv run python 05_gallery.py <nm_id>")
        return 3
    try:
        nm_id = int(sys.argv[1])
    except ValueError:
        err(f"nm_id must be int; got {sys.argv[1]!r}")
        return 3

    import httpx

    info(f"nm_id = {nm_id}")

    async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=8) as c:
        shard = await _resolve_shard(c, nm_id)
        if shard is None:
            err("could not find a basket shard for this nm_id (orphan or WB added new shards)")
            return 1
        info(f"shard = {shard}")

        # Get photo_count from card.json
        r = await c.get(card_json_url(nm_id, shard=shard))
        photo_count = (r.json().get("media") or {}).get("photo_count") if r.status_code == 200 else None
        info(f"card.json says photo_count = {photo_count}")

        # HEAD-verify the gallery — sometimes the count is stale.
        urls: list[str] = []
        with Timer() as t:
            for i in range(1, max((photo_count or 1) + 3, 30)):
                u = image_url(nm_id, i, shard=shard)
                try:
                    r = await c.head(u)
                except httpx.HTTPError:
                    break
                if r.status_code != 200:
                    if i > (photo_count or 0):    # past the known count, normal to 404
                        break
                    info(f"  [{i}] {r.status_code} {u}")
                    continue
                urls.append(u)
                info(f"  [{i}] OK  {u}")

    ok(f"verified {len(urls)} images in {t.elapsed_ms} ms")
    if photo_count and len(urls) != photo_count:
        warn(f"card.json said {photo_count}, but {len(urls)} are actually 200")

    path = save_json("05_gallery", {"nm_id": nm_id, "shard": shard, "count": len(urls), "urls": urls})
    ok(f"saved → {path}")
    return 0 if urls else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
