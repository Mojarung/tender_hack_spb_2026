"""Thin async Redis wrapper for query- and offer-level caching."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

import orjson
from redis.asyncio import Redis

T = TypeVar("T")


class RedisCache:
    def __init__(self, url: str) -> None:
        self._redis: Redis = Redis.from_url(url, decode_responses=False)

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def get(self, key: str) -> dict | list | None:
        raw = await self._redis.get(key)
        return orjson.loads(raw) if raw else None

    async def set(self, key: str, value: object, ttl_seconds: int) -> None:
        await self._redis.set(key, orjson.dumps(value, default=str), ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def get_or_set(
        self,
        key: str,
        producer: Callable[[], Awaitable[T]],
        ttl_seconds: int,
    ) -> T:
        cached = await self.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        value = await producer()
        await self.set(key, value, ttl_seconds=ttl_seconds)
        return value

    async def close(self) -> None:
        await self._redis.aclose()


# The route-level singleton lives in `pricepulse.api.cache` —
# `get_search_cache()` there pings Redis at construction and disables
# itself if the server is unreachable. Routes use that helper rather
# than instantiating this class directly.
