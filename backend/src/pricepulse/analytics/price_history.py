"""Accumulative price history store.

WB used to expose `wbx-content-v2.wbstatic.net/price-history/{nm}.json`,
but that host died sometime before May 2026. We capture our own series:
every time the WB (or any other) adapter parses an offer, we append
a `(timestamp, price)` point to a Redis sorted-set keyed by source+id.

The store is best-effort: failures NEVER bubble up — a flaky Redis must
not break a search.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import orjson
import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

_MAX_POINTS = 2000   # bounded — covers ~83 days at 1 sample/hour


def _key(source: str, item_id: str) -> str:
    return f"price-history:{source}:{item_id}"


@dataclass(frozen=True, slots=True)
class PricePoint:
    ts: datetime
    price: Decimal


class PriceHistoryStore:
    def __init__(self, redis: "Redis") -> None:
        self._redis = redis

    async def record(self, source: str, item_id: str, price: Decimal) -> None:
        if not item_id or price <= 0:
            return
        now = datetime.now(tz=UTC)
        member = orjson.dumps({"ts": now.isoformat(), "price": str(price)}).decode()
        try:
            pipe = self._redis.pipeline()
            pipe.zadd(_key(source, item_id), {member: now.timestamp()})
            pipe.zremrangebyrank(_key(source, item_id), 0, -(_MAX_POINTS + 1))
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            log.debug("price_history.record_failed", source=source, error=str(exc))

    async def get(self, source: str, item_id: str, limit: int = 200) -> list[PricePoint]:
        try:
            raw = await self._redis.zrange(_key(source, item_id), 0, -1)
        except Exception as exc:  # noqa: BLE001
            log.debug("price_history.read_failed", error=str(exc))
            return []
        points: list[PricePoint] = []
        for entry in raw[-limit:]:
            try:
                obj = orjson.loads(entry)
                points.append(PricePoint(
                    ts=datetime.fromisoformat(obj["ts"]),
                    price=Decimal(obj["price"]),
                ))
            except (ValueError, KeyError):
                continue
        return points

    async def latest(self, source: str, item_id: str) -> PricePoint | None:
        points = await self.get(source, item_id, limit=1)
        return points[-1] if points else None
