"""GET /api/v1/price-history/{source}/{item_id}.

Returns the accumulated price points captured by previous scrape runs.
For brand-new items the list is empty — the spark-line on the frontend
gracefully renders a single dot in that case.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis

from pricepulse.analytics.price_history import PriceHistoryStore
from pricepulse.api.deps import SettingsDep
from pricepulse.domain.enums import SourceKind

router = APIRouter(prefix="/price-history", tags=["search"])


async def _store(settings: SettingsDep) -> PriceHistoryStore:
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    return PriceHistoryStore(redis)


@router.get("/{source}/{item_id}")
async def price_history(
    source: SourceKind,
    item_id: str,
    limit: int = 200,
    store: PriceHistoryStore = Depends(_store),
) -> dict:
    if limit <= 0 or limit > 2000:
        raise HTTPException(status_code=422, detail="limit must be in (0, 2000]")
    points = await store.get(source.value, item_id, limit=limit)
    return {
        "source": source.value,
        "item_id": item_id,
        "count": len(points),
        "points": [
            {"ts": p.ts.isoformat(), "price": str(p.price)} for p in points
        ],
    }
