"""Unit tests for the token-bucket rate limiter (antibot/ratelimit.py)."""

import asyncio
import time

from pricepulse.antibot.ratelimit import RateLimiter


class _BoomRedis:
    """Redis client stand-in whose eval always fails — exercises the
    graceful degradation to the process-local bucket."""

    async def eval(self, *args: object, **kwargs: object) -> object:
        raise ConnectionError("redis down")

    async def aclose(self) -> None:
        return None


async def test_fresh_bucket_grants_immediately() -> None:
    rl = RateLimiter(None)
    start = time.monotonic()
    await rl.acquire("wb", rpm=60)
    assert time.monotonic() - start < 0.2


async def test_zero_rpm_is_noop() -> None:
    rl = RateLimiter(None)
    await rl.acquire("wb", rpm=0)  # must simply return without limiting


async def test_local_bucket_drains_then_blocks() -> None:
    rl = RateLimiter(None)
    # capacity 5, slow refill — the 5-token burst is free, the 6th is not.
    for _ in range(5):
        assert await rl._consume_local("k", 5.0, 5 / 60) == 0.0
    assert await rl._consume_local("k", 5.0, 5 / 60) > 0.0


async def test_local_bucket_refills_over_time() -> None:
    rl = RateLimiter(None)
    # capacity 1, fast refill (50 tokens/s) so the test stays quick.
    assert await rl._consume_local("k", 1.0, 50.0) == 0.0
    wait_ms = await rl._consume_local("k", 1.0, 50.0)
    assert wait_ms > 0.0
    await asyncio.sleep(wait_ms / 1000 + 0.05)
    assert await rl._consume_local("k", 1.0, 50.0) == 0.0


async def test_acquire_gives_up_after_max_wait() -> None:
    rl = RateLimiter(None)
    await rl.acquire("slow", rpm=1)  # consume the only token
    start = time.monotonic()
    # bucket is empty and refills 1/min — acquire must give up, not hang.
    await rl.acquire("slow", rpm=1, max_wait_s=0.1)
    assert time.monotonic() - start < 2.0


async def test_redis_error_degrades_to_local_bucket() -> None:
    rl = RateLimiter(_BoomRedis())
    await rl.acquire("wb", rpm=60)  # eval fails → must fall back, never raise
    assert rl._redis is None
