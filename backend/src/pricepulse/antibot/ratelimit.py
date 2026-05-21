"""Token-bucket rate limiter backed by Redis.

Key: ratelimit:{source}:{proxy_or_global}.
Algorithm: classic token-bucket implemented as a single Lua script in Redis
to be atomic across workers.
"""


class RateLimiter:
    """Stub. Real impl wires a Lua script + aioredis."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def acquire(self, key: str, rpm: int) -> None:
        # TODO (hackathon): SETEX-based bucket via Lua. If exhausted — sleep until next tick.
        return None
