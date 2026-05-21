"""Wildberries feedbacks fetcher — `feedbacks{1,2}.wb.ru/feedbacks/v1/{imt_id}`.

Live-verified May 2026: a single call returns up to ~1000 feedbacks
for a given `imt_id` (the `root` field on a search-API product). No
auth, no captcha. Both shards (`feedbacks1.wb.ru`, `feedbacks2.wb.ru`)
return the same payload — we hit one and fall back if it times out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_SHARDS = ("feedbacks1.wb.ru", "feedbacks2.wb.ru")

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
    """Subset of WB feedback fields we use for sentiment + aggregates."""

    id: str
    nm_id: int
    rating: int        # 1..5 (`productValuation`)
    text: str
    pros: str
    cons: str
    color: str
    size: str
    created: str       # ISO timestamp, kept raw for the UI
    pluses: int
    minuses: int

    @property
    def joined_text(self) -> str:
        """Combined text → suitable for sentiment classification."""
        return " ".join(filter(None, [self.text, self.pros, self.cons])).strip()


def _coerce(raw: dict[str, Any]) -> WbFeedback | None:
    nm_id = raw.get("nmId")
    if nm_id is None:
        return None
    votes = raw.get("votes") or {}
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
    )


async def fetch_wb_feedbacks(
    imt_id: int,
    *,
    limit: int = 200,
    timeout_s: float = 10.0,
) -> list[WbFeedback]:
    """Return up to `limit` feedbacks for the given imt_id, newest-first.

    The endpoint returns them in roughly insertion order; we trim to
    `limit` after the network round-trip. The full response holds ~1000
    items so this is cheap enough to do live.
    """
    last_error: Exception | None = None
    for host in _SHARDS:
        url = f"https://{host}/feedbacks/v1/{imt_id}"
        try:
            async with httpx.AsyncClient(http2=True, headers=_HEADERS, timeout=timeout_s) as c:
                resp = await c.get(url)
            if resp.status_code != 200 or not resp.content:
                continue
            payload = resp.json()
            raw = payload.get("feedbacks") or []
            items = [fb for raw_fb in raw if (fb := _coerce(raw_fb)) is not None]
            # WB returns oldest-first in some shards; sort by created date desc.
            items.sort(key=lambda fb: fb.created, reverse=True)
            log.info("wb_feedbacks.ok", imt=imt_id, returned=len(items), host=host)
            return items[:limit]
        except httpx.HTTPError as exc:
            last_error = exc
            log.debug("wb_feedbacks.shard_failed", host=host, error=str(exc))
            continue
    log.warning("wb_feedbacks.all_shards_failed", imt=imt_id, error=str(last_error))
    return []
