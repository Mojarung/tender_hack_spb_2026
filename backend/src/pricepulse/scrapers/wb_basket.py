"""Wildberries basket-CDN shard resolution + feedback media URL builders.

Two URL families live here:

  1. Product images / card.json: ``basket-{NN}.wbbasket.ru/vol{V}/part{P}/{nm}/...``
     where NN is derived from `nm_id // 100_000` via a known range table.
     Wildberries adds new shards as the catalog grows, so the table needs
     occasional extension. If a shard 404s, walk ±1..±5 around the
     extrapolated value (see :func:`wb_card.fetch_card`).

  2. Review media (photos + HLS video) on ``feedback-NN.wbbasket.ru`` /
     ``videofeedbackNN.wbbasket.ru``. The shard number lives in the
     `key` (photos) or `id` (videos) field of the feedback object —
     `"6/uuid"` → shard 06.

References:
- glmn/wb-private-api (CRC-16/ARC shard formula)
- Duff89/wildberries_parser (basket cascade)
- Live-verified May 2026 against feedback-06.wbbasket.ru and
  videofeedback03.wbbasket.ru.
"""

from __future__ import annotations

# Shard table extended to 35 (vol → basket) per the WB highload posts on
# Habr (May 2026). New shards land roughly quarterly — extend here when
# 02_card_detail.py's ±5 cascade starts hitting the upper bound.
_RANGES: tuple[tuple[int, str], ...] = (
    (143, "01"), (287, "02"), (431, "03"), (719, "04"),
    (1007, "05"), (1061, "06"), (1115, "07"), (1169, "08"),
    (1313, "09"), (1601, "10"), (1655, "11"), (1919, "12"),
    (2045, "13"), (2189, "14"), (2405, "15"), (2621, "16"),
    (2837, "17"), (3053, "18"), (3269, "19"), (3485, "20"),
    (3701, "21"), (3917, "22"), (4133, "23"), (4349, "24"),
    (4565, "25"), (4877, "26"), (5189, "27"), (5501, "28"),
    (5813, "29"), (6125, "30"), (6437, "31"), (6749, "32"),
    (7061, "33"), (7373, "34"), (7685, "35"),
)


def basket_for(nm_id: int) -> str:
    """Return basket shard `XX` (string) for given Wildberries nm_id.

    Past the last known range we extrapolate; callers should treat the
    result as a starting point and walk ±1..±5 on 404 (see
    `wb_card.fetch_card` for the canonical cascade)."""
    vol = nm_id // 100_000
    for upper, basket in _RANGES:
        if vol <= upper:
            return basket
    # Linear extrapolation past the table
    last_upper, last_shard = _RANGES[-1]
    extra = max(0, (vol - last_upper) // 312)
    return f"{int(last_shard) + 1 + extra:02d}"


def card_json_url(nm_id: int, shard: str | None = None) -> str:
    """URL of `info/ru/card.json` for given nm_id."""
    if shard is None:
        shard = basket_for(nm_id)
    vol = nm_id // 100_000
    part = nm_id // 1_000
    return (
        f"https://basket-{shard}.wbbasket.ru"
        f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    )


def image_url(
    nm_id: int,
    idx: int = 1,
    *,
    size: str = "big",
    shard: str | None = None,
) -> str:
    """Product gallery image URL.

    `idx` is 1..N where N comes from card.json `media.photo_count`.
    `size` ∈ {big, c516x688, c246x328, square, tm}.
    """
    if shard is None:
        shard = basket_for(nm_id)
    vol = nm_id // 100_000
    part = nm_id // 1_000
    return (
        f"https://basket-{shard}.wbbasket.ru"
        f"/vol{vol}/part{part}/{nm_id}/images/{size}/{idx}.webp"
    )


def price_history_url(nm_id: int) -> str:
    """Multi-year price history JSON (kopecks). Returns 404 for some items."""
    return f"https://wbx-content-v2.wbstatic.net/price-history/{nm_id}.json"


# ---------------------------------------------------------------------------
# Feedback media — different host family, sharded by leading digit
# of the photo/video `key` (e.g. "6/uuid" → 06).
# ---------------------------------------------------------------------------
def feedback_photo_urls(key: str) -> dict[str, str]:
    """Build mini / full / jpg URLs for a feedback photo.

    `key` looks like ``"6/d7a25475-cd60-412a-985f-11007bf8d84f"``;
    the prefix is the basket shard. Note the dash in `feedback-NN`
    (vs no-dash legacy hosts) and the lack of a `/photos/` path
    segment — both are 2024+ changes."""
    shard_str, uuid = key.split("/", 1)
    shard = int(shard_str)
    base = f"https://feedback-{shard:02d}.wbbasket.ru/{uuid}"
    return {
        "mini": f"{base}/ms.webp",
        "full": f"{base}/fs.webp",
        "jpg":  f"{base}/fs.jpg",
    }


def feedback_video_urls(video_id: str) -> dict[str, str]:
    """Build HLS playlist + preview poster URLs for a feedback video.

    `video_id` looks like ``"3/f2d03473-..."``. Note **no dash** in
    ``videofeedbackNN`` (different from photos). WB serves videos as
    HLS-segmented VOD — there's no single .mp4. Frontend needs hls.js
    OR a server-side ffmpeg mux to play it back."""
    shard_str, uuid = video_id.split("/", 1)
    shard = int(shard_str)
    base = f"https://videofeedback{shard:02d}.wbbasket.ru/{uuid}"
    return {
        "preview": f"{base}/preview.webp",
        "hls":     f"{base}/index.m3u8",
    }


def feedbacks_host(imt_id: int) -> str:
    """CRC-16/ARC mod 100 → "feedbacks1.wb.ru" or "feedbacks2.wb.ru".

    Both shards serve the same payload; matching what wildberries.ru's
    own JS computes keeps the request indistinguishable from a real
    browser hit. Picker derived from glmn/wb-private-api."""
    crc = 0
    for b in imt_id.to_bytes(8, "little"):
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return "feedbacks2.wb.ru" if crc % 100 >= 50 else "feedbacks1.wb.ru"


__all__ = [
    "basket_for",
    "card_json_url",
    "feedback_photo_urls",
    "feedback_video_urls",
    "feedbacks_host",
    "image_url",
    "price_history_url",
]
