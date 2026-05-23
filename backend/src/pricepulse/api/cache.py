import structlog

from pricepulse.cache.redis_cache import RedisCache
from pricepulse.config import get_settings

log = structlog.get_logger(__name__)

_cache: RedisCache | None = None
_cache_failed = False


async def get_search_cache() -> RedisCache | None:
    global _cache, _cache_failed
    if _cache is not None:
        return _cache
    if _cache_failed:
        return None

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
