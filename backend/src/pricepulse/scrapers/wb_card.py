"""Wildberries basket-CDN card.json fetcher + parsers.

`basket-{NN}.wbbasket.ru/vol{V}/part{P}/{nm}/info/ru/card.json` is
the canonical product detail JSON: chars, description, imt_id,
media.photo_count, brand. Not behind any PoW / Page Guard — just
static CDN. Auto-retries ±1..±5 around the computed shard since WB
adds new shards every few months.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from pricepulse.scrapers.wb_basket import basket_for, card_json_url, image_url

log = structlog.get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


@dataclass(slots=True)
class WbCardDetail:
    """Subset of card.json fields we surface in ProductOffer."""

    nm_id: int
    shard: str
    imt_id: int | None
    imt_name: str
    description: str
    brand: str | None
    category_root: str | None
    category: str | None
    photo_count: int
    has_video: bool
    # [(group_name, attr_name, attr_value), ...] — group is "" if flat
    characteristics: list[tuple[str, str, str]] = field(default_factory=list)
    # Full gallery URLs built from photo_count + shard
    gallery: list[str] = field(default_factory=list)
    # Best-effort post-discount price in rubles, or None
    price_rub: int | None = None


def _flatten_chars(card: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Pull (group, name, value) triples. `grouped_options` is preferred
    (lets the UI render section headers); falls back to flat `options[]`.
    Defensive against int/string entries that occasionally appear in
    place of dicts (WB card.json is not strict about schemas)."""
    out: list[tuple[str, str, str]] = []
    for grp in card.get("grouped_options") or []:
        if not isinstance(grp, dict):
            continue
        gn = (grp.get("group_name") or "").strip()
        for opt in grp.get("options") or []:
            if not isinstance(opt, dict):
                continue
            name = (opt.get("name") or "").strip()
            value = str(opt.get("value") or "").strip()
            if name and value:
                out.append((gn, name, value))
    if not out:
        for opt in card.get("options") or []:
            if not isinstance(opt, dict):
                continue
            name = (opt.get("name") or "").strip()
            value = str(opt.get("value") or "").strip()
            if name and value:
                out.append(("", name, value))
    return out


def _price_from_card(card: dict[str, Any]) -> int | None:
    """Best-effort post-discount price in RUB.

    Tries `extended.clientPriceU` (canonical, post-discount) first, then
    `basicPriceU`, then digs into per-color/per-size price blocks. All
    values are in kopeyki (multiply by 10⁻²). Sanity ceiling 5M ₽.

    Defensive: some cards have `colors` as a list of bare int IDs
    (referring to a color dict elsewhere), or `sizes` mixed with
    string slugs — skip non-dict entries instead of crashing."""
    ext = card.get("extended") or {}
    if isinstance(ext, dict):
        for key in ("clientPriceU", "discountPriceU", "basicPriceU"):
            v = ext.get(key)
            if isinstance(v, (int, float)) and 0 < v < 5_000_000_00:
                return int(v) // 100
    for col in card.get("colors") or []:
        if not isinstance(col, dict):
            continue
        for sz in col.get("sizes") or []:
            if not isinstance(sz, dict):
                continue
            pr = sz.get("price") or {}
            if not isinstance(pr, dict):
                continue
            total = pr.get("product") or pr.get("basic") or pr.get("total")
            if isinstance(total, (int, float)) and 0 < total < 5_000_000_00:
                return int(total) // 100
    return None


def _parse_card(nm_id: int, shard: str, raw: dict[str, Any]) -> WbCardDetail:
    media = raw.get("media") or {}
    photo_count = int(media.get("photo_count") or 0)
    gallery = [
        image_url(nm_id, i, shard=shard) for i in range(1, photo_count + 1)
    ]
    selling = raw.get("selling") or {}
    return WbCardDetail(
        nm_id=nm_id,
        shard=shard,
        imt_id=raw.get("imt_id"),
        imt_name=(raw.get("imt_name") or "")[:300],
        description=(raw.get("description") or "")[:2000],
        brand=selling.get("brand_name") or raw.get("brand"),
        category_root=raw.get("subj_root_name"),
        category=raw.get("subj_name"),
        photo_count=photo_count,
        has_video=bool(media.get("has_video")),
        characteristics=_flatten_chars(raw),
        gallery=gallery,
        price_rub=_price_from_card(raw),
    )


async def fetch_card(
    nm_id: int,
    *,
    timeout_s: float = 8.0,
    client: httpx.AsyncClient | None = None,
) -> WbCardDetail | None:
    """Fetch + parse card.json with ±5 shard cascade.

    Pass a shared `client` when calling in a fan-out — saves HTTP/2
    connection setup per request."""
    primary = int(basket_for(nm_id))
    candidates: list[int] = [primary]
    for d in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
        n = primary + d
        if 1 <= n <= 60 and n not in candidates:
            candidates.append(n)

    own_client = client is None
    c = client or httpx.AsyncClient(http2=True, headers=_HEADERS, timeout=timeout_s)
    try:
        for nn in candidates:
            url = card_json_url(nm_id, shard=f"{nn:02d}")
            try:
                resp = await c.get(url)
            except httpx.HTTPError as exc:
                log.debug("wb_card.shard_network_error", nm=nm_id, shard=nn, error=str(exc))
                continue
            if resp.status_code != 200 or not resp.content:
                continue
            try:
                raw = resp.json()
            except ValueError:
                continue
            return _parse_card(nm_id, f"{nn:02d}", raw)
        log.warning("wb_card.all_shards_404", nm=nm_id, primary=primary)
        return None
    finally:
        if own_client:
            await c.aclose()


__all__ = ["WbCardDetail", "fetch_card"]
