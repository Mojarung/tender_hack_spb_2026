"""Search orchestrator — fan-out, safe-wrap, optional streaming.

* Always parallel — `asyncio.gather` with per-adapter exception isolation.
* Optional streaming — caller passes `on_offer` and we wire it through.
* Optional cache — passed in from the FastAPI dependency layer; if `None`,
  the orchestrator runs cache-less (e.g. in tests).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog

from pricepulse.cache.redis_cache import RedisCache
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer, SourceGroup
from pricepulse.enrichment.normalize import normalize_query
from pricepulse.scrapers.base import ScraperProtocol, ScrapeResult
from pricepulse.scrapers.ozon import OzonScraper
from pricepulse.scrapers.runet import RunetScraper
from pricepulse.scrapers.megamarket import MegamarketScraper
from pricepulse.scrapers.wb import WildberriesScraper
from pricepulse.scrapers.yandex_market import YandexMarketScraper

log = structlog.get_logger(__name__)

# TTLs per source — anti-bot.md §9. WB updates faster than the rest.
_CACHE_TTL: dict[SourceKind, int] = {
    SourceKind.WB: 60 * 60,           # 1h
    SourceKind.OZON: 6 * 60 * 60,     # 6h
    SourceKind.YA_MARKET: 6 * 60 * 60,
    SourceKind.RUNET: 12 * 60 * 60,
}


class SearchOrchestrator:
    """Fan-out to every registered source. One instance per request is fine."""

    def __init__(
        self,
        *,
        cache: RedisCache | None = None,
        adapters: dict[SourceKind, ScraperProtocol] | None = None,
        runet_fallback: ScraperProtocol | None = None,
    ) -> None:
        # Default registry. Runet hits Firecrawl when an API key is configured;
        # otherwise we transparently fall back to MegamarketScraper.
        self._registry: dict[SourceKind, ScraperProtocol] = adapters or {
            SourceKind.WB: WildberriesScraper(),
            SourceKind.OZON: OzonScraper(),
            SourceKind.YA_MARKET: YandexMarketScraper(),
            SourceKind.RUNET: RunetScraper(),
        }
        self._cache = cache
        self._runet_fallback = runet_fallback or MegamarketScraper()

    def _pick(self, sources: list[SourceKind] | None) -> list[ScraperProtocol]:
        if not sources:
            return list(self._registry.values())
        return [self._registry[s] for s in sources if s in self._registry]

    # ──────────────────────────────────────── public API ─────────────────────

    async def run(
        self,
        query: str,
        max_per_source: int,
        sources: list[SourceKind] | None = None,
    ) -> tuple[NormalizedQuery, list[SourceGroup]]:
        normalized = await normalize_query(query)
        adapters = self._pick(sources)
        results = await asyncio.gather(
            *[self._safe_call(a, normalized, max_per_source) for a in adapters]
        )
        return normalized, [_to_group(r) for r in results]

    async def stream(
        self,
        query: str,
        max_per_source: int,
        sources: list[SourceKind] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yields SSE-shaped events as adapters report offers."""
        started = time.perf_counter()
        normalized = await normalize_query(query)
        yield "query_normalized", normalized.model_dump()

        adapters = self._pick(sources)
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def _drive(adapter: ScraperProtocol) -> None:
            await queue.put(("source_started", {"source": adapter.source.value}))

            async def on_offer(offer: ProductOffer) -> None:
                await queue.put((
                    "offer",
                    {"source": adapter.source.value, "offer": offer.model_dump(mode="json")},
                ))

            result = await self._safe_call(adapter, normalized, max_per_source, on_offer=on_offer)
            await queue.put((
                "source_finished",
                {
                    "source": adapter.source.value,
                    "count": len(result.offers),
                    "min_price": str(min((o.price for o in result.offers), default=""))
                    if result.offers else None,
                    "error": result.error,
                    "cached": result.cached,
                },
            ))

        async def _drain() -> None:
            await asyncio.gather(*[_drive(a) for a in adapters])
            await queue.put(None)   # sentinel

        drainer = asyncio.create_task(_drain())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            drainer.cancel()
        took_ms = int((time.perf_counter() - started) * 1000)
        yield "done", {"took_ms": took_ms}

    # ──────────────────────────────────────── internals ──────────────────────

    async def _safe_call(
        self,
        adapter: ScraperProtocol,
        normalized: NormalizedQuery,
        limit: int,
        on_offer=None,
    ) -> ScrapeResult:
        cache_key = f"cache:{adapter.source.value}:{normalized.normalized}:{limit}"
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached:
                offers = [ProductOffer.model_validate(o) for o in cached.get("offers", [])]
                if on_offer is not None:
                    for o in offers:
                        await on_offer(o)
                return ScrapeResult(source=adapter.source, offers=offers, cached=True)

        try:
            result = await adapter.search(normalized, limit=limit, on_offer=on_offer)
        except Exception as exc:  # noqa: BLE001 — never propagate, isolate sources
            log.warning("orchestrator.adapter_crash",
                        source=adapter.source.value, error=str(exc))
            # Runet has a deterministic fallback (Megamarket)
            if adapter.source == SourceKind.RUNET:
                try:
                    result = await self._runet_fallback.search(
                        normalized, limit=limit, on_offer=on_offer
                    )
                except Exception as fb_exc:  # noqa: BLE001
                    return ScrapeResult(
                        source=adapter.source, offers=[],
                        error=f"runet+fallback failed: {fb_exc}",
                    )
            else:
                return ScrapeResult(source=adapter.source, offers=[], error=str(exc))

        # If primary RunetScraper returned nothing, try Megamarket
        if adapter.source == SourceKind.RUNET and not result.offers and not result.error:
            result = await self._runet_fallback.search(
                normalized, limit=limit, on_offer=on_offer
            )

        # Populate cache on success
        if self._cache is not None and result.offers and not result.error:
            ttl = _CACHE_TTL.get(adapter.source, 3600)
            await self._cache.set(
                cache_key,
                {"offers": [o.model_dump(mode="json") for o in result.offers]},
                ttl_seconds=ttl,
            )
        return result


def _to_group(result: ScrapeResult) -> SourceGroup:
    offers = result.offers
    min_price = min((o.price for o in offers), default=None)
    return SourceGroup(
        source=result.source,
        count=len(offers),
        min_price=min_price,
        offers=offers,
        error=result.error,
    )
