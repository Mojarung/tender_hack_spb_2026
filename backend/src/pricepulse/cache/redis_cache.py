"""Thin async Redis wrapper for query-level caching."""

import orjson
from redis.asyncio import Redis


class RedisCache:
    def __init__(self, url: str) -> None:
        self._redis = Redis.from_url(url, decode_responses=False)

    async def get(self, key: str) -> dict | list | None:
        raw = await self._redis.get(key)
        return orjson.loads(raw) if raw else None

    async def set(self, key: str, value: object, ttl_seconds: int) -> None:
        await self._redis.set(key, orjson.dumps(value), ex=ttl_seconds)

    async def close(self) -> None:
        await self._redis.aclose()
