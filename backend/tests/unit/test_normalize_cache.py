"""normalize_query caches the whole result by raw query — SAGE inference
(the ~500 ms part) never reruns for an identical input."""

from __future__ import annotations

from typing import Any

import pytest

from pricepulse.enrichment.normalize import normalize_query


class _FakeRedisCache:
    """In-memory stand-in for :class:`RedisCache` — same async API."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, key: str) -> Any | None:
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: Any, *, ttl_seconds: int = 0) -> None:
        self.set_calls += 1
        self.store[key] = value


@pytest.mark.asyncio
async def test_first_call_writes_cache() -> None:
    cache = _FakeRedisCache()
    await normalize_query("наушники сони", cache=cache)
    assert cache.set_calls == 1
    assert len(cache.store) == 1


@pytest.mark.asyncio
async def test_repeated_call_hits_cache() -> None:
    cache = _FakeRedisCache()
    n1 = await normalize_query("наушники сони", cache=cache)

    # Replace the cached payload with a sentinel — if the second call
    # short-circuits via cache, we get back the sentinel.
    [key] = list(cache.store)
    sentinel = {
        "raw": "наушники сони",
        "normalized": "SENTINEL",
        "expansions": [],
        "alternates": [],
    }
    cache.store[key] = sentinel

    n2 = await normalize_query("наушники сони", cache=cache)
    assert n2.normalized == "SENTINEL"
    # And we did not write again (it was a hit, not a miss).
    assert cache.set_calls == 1
    # We did read on every call (initial + the hit).
    assert cache.get_calls >= 2

    # First result is untouched by the sentinel injection.
    assert n1.normalized != "SENTINEL"


@pytest.mark.asyncio
async def test_different_raw_misses_cache() -> None:
    cache = _FakeRedisCache()
    await normalize_query("наушники сони", cache=cache)
    await normalize_query("наушники jbl", cache=cache)
    assert len(cache.store) == 2


@pytest.mark.asyncio
async def test_nofix_uses_separate_cache_key() -> None:
    """`fix=True` and `fix=False` produce different normalisations, so they
    must live under different cache keys."""
    cache = _FakeRedisCache()
    await normalize_query("Айфон 15", fix=True, cache=cache)
    await normalize_query("Айфон 15", fix=False, cache=cache)
    assert len(cache.store) == 2


@pytest.mark.asyncio
async def test_cache_failure_does_not_break_normalize() -> None:
    """If the cache raises, normalize_query proceeds without caching."""

    class _BoomCache:
        async def get(self, key: str) -> Any:
            raise ConnectionError("redis down")

        async def set(self, key: str, value: Any, *, ttl_seconds: int = 0) -> None:
            raise ConnectionError("redis down")

    n = await normalize_query("наушники", cache=_BoomCache())
    assert n.normalized == "наушники"
