"""Wildberries feedbacks v2 — reviews with photo + video URLs.

Live-verified May 2026:
  GET https://feedbacks{1,2}.wb.ru/feedbacks/v2/{imt_id}
returns up to ~1000 feedbacks plus per-nm rating histogram + total
counts (text/photo/video). v1 is still served but lacks
`nmValuationDistribution` and the modern photo `key` field shape.

The host shard is chosen by CRC-16/ARC over imt_id (matches what
wildberries.ru's JS does). Both shards return identical payloads;
we use the canonical pick and fall back to the other on timeout.

No auth, no captcha, no rate limit headers visible at ~5 RPS. PG-XX
guard is on the search.wb.ru host, not on feedbacks{1,2}.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from pricepulse.scrapers.wb_basket import (
    feedback_photo_urls,
    feedback_video_urls,
    feedbacks_host,
)

log = structlog.get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}


@dataclass(frozen=True, slots=True)
class WbFeedback:
    """Subset of WB feedback fields we surface in the API + modal.

    `photo_urls`/`video_urls` are built locally from the raw `photos[].key`
    / `video.id` fields — no extra network call needed."""

    id: str
    nm_id: int
    rating: int                              # 1..5 (`productValuation`)
    text: str
    pros: str
    cons: str
    color: str
    size: str
    created: str                             # ISO timestamp
    pluses: int
    minuses: int
    photo_urls: list[dict[str, str]] = field(default_factory=list)
    video_urls: dict[str, str] | None = None

    @property
    def joined_text(self) -> str:
        return " ".join(filter(None, [self.text, self.pros, self.cons])).strip()


def _coerce(raw: dict[str, Any]) -> WbFeedback | None:
    nm_id = raw.get("nmId")
    if nm_id is None:
        return None

    votes = raw.get("votes") or {}

    # Photo URLs from `photos[].key` (modern) — skip not-ready entries
    photo_urls: list[dict[str, str]] = []
    for p in raw.get("photos") or []:
        if isinstance(p, dict) and p.get("isReady", True):
            key = p.get("key")
            if isinstance(key, str) and "/" in key:
                try:
                    photo_urls.append(feedback_photo_urls(key))
                except (ValueError, IndexError):
                    pass

    # Video URLs from `video.id` if present and ready
    video_urls: dict[str, str] | None = None
    v = raw.get("video")
    if isinstance(v, dict) and v.get("isReady"):
        vid = v.get("id")
        if isinstance(vid, str) and "/" in vid:
            try:
                video_urls = feedback_video_urls(vid)
            except (ValueError, IndexError):
                video_urls = None

    return WbFeedback(
        id=str(raw.get("id") or ""),
        nm_id=int(nm_id),
        rating=int(raw.get("productValuation") or 0),
        text=(raw.get("text") or "").strip(),
        pros=(raw.get("pros") or "").strip(),
        cons=(raw.get("cons") or "").strip(),
        color=(raw.get("color") or "").strip(),
        size=(raw.get("size") or "").strip(),
        created=str(raw.get("createdDate") or ""),
        pluses=int(votes.get("pluses") or 0),
        minuses=int(votes.get("minuses") or 0),
        photo_urls=photo_urls,
        video_urls=video_urls,
    )


@dataclass(frozen=True, slots=True)
class WbFeedbacksPage:
    """Result of one feedbacks endpoint call. Holds both per-review
    items and the rollup counts the modal header needs."""

    feedbacks: list[WbFeedback]
    total: int                              # `feedbackCount` — true total
    with_photo: int
    with_video: int
    valuation: float | None                 # average score 0..5
    valuation_distribution: dict[str, int]


async def fetch_wb_feedbacks(
    imt_id: int,
    *,
    limit: int = 200,
    timeout_s: float = 10.0,
    prefer_v2: bool = True,
) -> WbFeedbacksPage:
    """Return up to `limit` feedbacks (newest-first) + page rollups.

    Tries v2 on both shards (correct CRC pick first, then the other),
    falls back to v1 if both v2 attempts fail. Empty list on total
    failure — never raises."""
    primary = feedbacks_host(imt_id)
    fallback = "feedbacks1.wb.ru" if primary == "feedbacks2.wb.ru" else "feedbacks2.wb.ru"
    versions = ("v2", "v1") if prefer_v2 else ("v1",)

    last_error: Exception | None = None
    async with httpx.AsyncClient(http2=True, headers=_HEADERS, timeout=timeout_s) as c:
        for ver in versions:
            for host in (primary, fallback):
                url = f"https://{host}/feedbacks/{ver}/{imt_id}"
                try:
                    resp = await c.get(url)
                except httpx.HTTPError as exc:
                    last_error = exc
                    log.debug("wb_feedbacks.shard_failed", host=host, ver=ver, error=str(exc))
                    continue
                if resp.status_code != 200 or not resp.content:
                    continue
                payload = resp.json()
                raw = payload.get("feedbacks") or []
                items: list[WbFeedback] = [
                    fb for raw_fb in raw if (fb := _coerce(raw_fb)) is not None
                ]
                items.sort(key=lambda fb: fb.created, reverse=True)
                log.info(
                    "wb_feedbacks.ok",
                    imt=imt_id, ver=ver, host=host,
                    returned=len(items), total=payload.get("feedbackCount"),
                )
                return WbFeedbacksPage(
                    feedbacks=items[:limit],
                    total=int(payload.get("feedbackCount") or 0),
                    with_photo=int(payload.get("feedbackCountWithPhoto") or 0),
                    with_video=int(payload.get("feedbackCountWithVideo") or 0),
                    valuation=float(payload.get("valuation") or 0) or None,
                    valuation_distribution=payload.get("valuationDistribution") or {},
                )

    log.warning("wb_feedbacks.all_shards_failed", imt=imt_id, error=str(last_error))
    return WbFeedbacksPage(
        feedbacks=[], total=0, with_photo=0, with_video=0,
        valuation=None, valuation_distribution={},
    )


__all__ = ["WbFeedback", "WbFeedbacksPage", "fetch_wb_feedbacks"]
