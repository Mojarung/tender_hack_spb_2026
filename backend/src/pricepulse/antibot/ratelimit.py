"""Token-bucket rate limiter backed by Redis.

A per-source token bucket implemented as a single atomic Lua script, so
that concurrent workers share one request budget. If Redis is unreachable
the limiter degrades to a process-local bucket — scraping is never
blocked by the limiter itself being down.

Bucket per key `ratelimit:{source}`:
  * capacity (burst) = rpm
  * refill rate      = rpm / 60 tokens per second

See CLAUDE.md → Anti-bot слой — this is the L0 "don't get blocked in
the first place" layer of the cascade.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# KEYS[1] = bucket key
# ARGV[1] = capacity (max tokens / burst)
# ARGV[2] = refill rate (tokens per second)
# ARGV[3] = now (unix seconds, float)
# ARGV[4] = tokens requested (usually 1)
# Returns: wait_ms — 0 if a token was granted, else ms until one frees up.
_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local want = tonumber(ARGV[4])

local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
    tokens = capacity
    ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * rate)

local wait_ms = 0
if tokens >= want then
    tokens = tokens - want
else
    wait_ms = math.ceil(((want - tokens) / rate) * 1000)
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
-- expire idle buckets so we never leak keys
redis.call('PEXPIRE', key, math.ceil((capacity / rate) * 1000) + 1000)
return wait_ms
"""


class RateLimiter:
    """Async token-bucket limiter. Construct with a redis client, or use
    :meth:`from_url`. Pass ``None`` for a purely process-local limiter."""

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._local: dict[str, tuple[float, float]] = {}  # key -> (tokens, ts)
        self._lock = asyncio.Lock()

    @classmethod
    def from_url(cls, redis_url: str) -> RateLimiter:
        """Build a Redis-backed limiter. Never raises — falls back to a
        local limiter if the redis client cannot be constructed."""
        try:
            from redis.asyncio import Redis

            return cls(Redis.from_url(redis_url, decode_responses=True))
        except Exception as exc:  # degrade, never crash startup
            log.warning("ratelimit.redis_unavailable", error=str(exc))
            return cls(None)

    async def acquire(self, key: str, rpm: int, *, max_wait_s: float = 30.0) -> None:
        """Block until a request token for `key` is available.

        `rpm` is requests-per-minute (bucket capacity + refill basis).
        Gives up after `max_wait_s` and lets the request through rather
        than stalling a user-facing search forever.
        """
        if rpm <= 0:
            return
        capacity = float(rpm)
        rate = rpm / 60.0
        bucket = f"ratelimit:{key}"
        deadline = time.monotonic() + max_wait_s
        while True:
            wait_ms = await self._consume(bucket, capacity, rate)
            if wait_ms <= 0:
                return
            sleep_s = min(wait_ms / 1000.0, 1.0)
            if time.monotonic() + sleep_s > deadline:
                log.warning("ratelimit.max_wait_exceeded", key=key, rpm=rpm)
                return
            await asyncio.sleep(sleep_s)

    async def _consume(self, bucket: str, capacity: float, rate: float) -> float:
        if self._redis is not None:
            try:
                now = time.time()
                res = await self._redis.eval(
                    _BUCKET_LUA, 1, bucket, capacity, rate, now, 1,
                )
                return float(res)
            except Exception as exc:  # redis down → degrade to local bucket
                log.warning("ratelimit.redis_error", error=str(exc))
                self._redis = None
        return await self._consume_local(bucket, capacity, rate)

    async def _consume_local(self, bucket: str, capacity: float, rate: float) -> float:
        async with self._lock:
            now = time.time()
            tokens, ts = self._local.get(bucket, (capacity, now))
            tokens = min(capacity, tokens + max(0.0, now - ts) * rate)
            if tokens >= 1.0:
                self._local[bucket] = (tokens - 1.0, now)
                return 0.0
            self._local[bucket] = (tokens, now)
            return ((1.0 - tokens) / rate) * 1000.0

    async def aclose(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as exc:  # best-effort close
                log.warning("ratelimit.close_failed", error=str(exc))


__all__ = ["RateLimiter"]
