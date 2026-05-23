"""Process-wide singletons the API routes inject into the orchestrator.

Two singletons live here:

* :func:`get_search_cache` — :class:`RedisCache` for query- and offer-level
  caching. Pings Redis at construction; on failure the singleton is sticky-
  ``None`` so the rest of the app runs cache-less without ever retrying.
* :func:`get_rate_limiter` — :class:`RateLimiter` for token-bucket rate
  limiting per source, shared between every uvicorn worker so the
  effective RPS hitting a marketplace is one budget, not N × budget. The
  limiter itself falls back to a process-local bucket when Redis is down.

Both lazily construct under an asyncio lock so two concurrent first
requests never produce two clients.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from pricepulse.cache.redis_cache import RedisCache
from pricepulse.config import get_settings
from pricepulse.storage.s3 import ImageCache

log = structlog.get_logger(__name__)

_cache: RedisCache | None = None
_cache_failed = False
_cache_lock = asyncio.Lock()

_limiter: Any = None      # RateLimiter — lazy-imported to avoid cycles
_limiter_lock = asyncio.Lock()

_image_cache: ImageCache | None = None


async def get_search_cache() -> RedisCache | None:
    """Return the cache singleton or ``None`` if Redis is unreachable."""
    global _cache, _cache_failed
    if _cache is not None or _cache_failed:
        return _cache
    async with _cache_lock:
        if _cache is not None or _cache_failed:   # double-check under the lock
            return _cache
        settings = get_settings()
        cache = RedisCache(settings.redis_url)
        try:
            await cache.ping()
        except Exception as exc:
            _cache_failed = True
            await cache.close()
            log.warning("search_cache.unavailable", redis_url=settings.redis_url, error=str(exc))
            return None
        _cache = cache
        log.info("search_cache.enabled", redis_url=settings.redis_url)
        return _cache


async def close_search_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.close()
        _cache = None


async def get_rate_limiter() -> Any:
    """Return the RateLimiter singleton — Redis-backed when reachable, local
    otherwise. Imports lazily to keep the cache module free of an antibot
    dependency at import time."""
    global _limiter
    if _limiter is not None:
        return _limiter
    async with _limiter_lock:
        if _limiter is not None:
            return _limiter
        from pricepulse.antibot.ratelimit import RateLimiter

        _limiter = RateLimiter.from_url(get_settings().redis_url)
        log.info("rate_limiter.created", redis_url=get_settings().redis_url)
        return _limiter


async def close_rate_limiter() -> None:
    global _limiter
    if _limiter is None:
        return
    try:
        await _limiter.aclose()
    except Exception as exc:
        log.debug("rate_limiter.close_failed", error=str(exc))
    _limiter = None


def get_image_cache() -> ImageCache | None:
    """Return the singleton ImageCache, or ``None`` when disabled by config.

    No ping at construction — `ensure_cached` already deals gracefully with
    a MinIO/S3 outage by returning ``None`` (callers fall back to the
    original URL), so a flaky bucket never wedges the API.
    """
    global _image_cache
    if _image_cache is not None:
        return _image_cache
    settings = get_settings()
    if not settings.image_cache_enabled:
        return None
    _image_cache = ImageCache(
        endpoint=settings.s3_endpoint_url,
        public_url=settings.s3_public_url or settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket=settings.s3_bucket,
        region=settings.s3_region,
    )
    log.info("image_cache.enabled", bucket=settings.s3_bucket)
    return _image_cache
