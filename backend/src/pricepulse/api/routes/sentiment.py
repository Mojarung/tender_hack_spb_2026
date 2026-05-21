"""GET /api/v1/sentiment/{source}/{item_id}.

For a given product, fetches recent feedbacks from the source-specific
endpoint, runs `seara/rubert-tiny2-russian-sentiment` over each
combined-text and returns the aggregate breakdown plus a few sample
quotes per bucket. Heavy lifting is cached per-text in Redis (24h).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis

from pricepulse.analytics.sentiment import (
    SentimentBreakdown,
    SentimentResult,
    aggregate,
    classify_batch,
    empty_breakdown,
    is_available,
)
from pricepulse.api.deps import SettingsDep
from pricepulse.domain.enums import SourceKind
from pricepulse.scrapers.wb_feedbacks import WbFeedback, fetch_wb_feedbacks

router = APIRouter(prefix="/sentiment", tags=["search"])


async def _redis(settings: SettingsDep) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=False)


def _pick_quotes(
    feedbacks: list[WbFeedback], results: list[SentimentResult]
) -> dict[str, list[dict]]:
    """Take up to 3 most upvoted feedbacks per sentiment label."""
    buckets: dict[str, list[tuple[WbFeedback, float]]] = {"positive": [], "neutral": [], "negative": []}
    for fb, res in zip(feedbacks, results, strict=False):
        text = fb.joined_text
        if not text:
            continue
        buckets[res.label].append((fb, res.score))
    out: dict[str, list[dict]] = {}
    for label, items in buckets.items():
        items.sort(key=lambda x: (-x[0].pluses, -x[1]))
        out[label] = [
            {
                "text": fb.joined_text[:240],
                "rating": fb.rating,
                "votes_plus": fb.pluses,
                "created": fb.created,
                "score": round(score, 3),
            }
            for fb, score in items[:3]
        ]
    return out


@router.get("/{source}/{item_id}")
async def sentiment(
    source: SourceKind,
    item_id: int,
    sample: int = Query(default=100, ge=10, le=500),
    redis: Redis = Depends(_redis),
) -> dict:
    if source != SourceKind.WB:
        raise HTTPException(
            status_code=501,
            detail=(
                f"sentiment for source={source.value} is not implemented yet "
                "(feedbacks fetcher available only for WB in this MVP)"
            ),
        )

    feedbacks = await fetch_wb_feedbacks(item_id, limit=sample)
    if not feedbacks:
        return {
            "source": source.value,
            "item_id": item_id,
            "available": is_available(),
            "breakdown": asdict(empty_breakdown()),
            "quotes": {"positive": [], "neutral": [], "negative": []},
            "feedbacks_seen": 0,
        }

    texts = [fb.joined_text for fb in feedbacks]
    results = await classify_batch(texts, redis=redis)
    breakdown: SentimentBreakdown = aggregate(
        [r for r, t in zip(results, texts, strict=True) if t.strip()]
    )
    quotes = _pick_quotes(feedbacks, results)

    return {
        "source": source.value,
        "item_id": item_id,
        "available": is_available(),
        "breakdown": asdict(breakdown),
        "quotes": quotes,
        "feedbacks_seen": len(feedbacks),
    }
