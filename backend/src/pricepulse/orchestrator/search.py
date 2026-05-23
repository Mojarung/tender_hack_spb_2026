"""Search orchestrator — fan-out, safe-wrap, optional streaming.

* Always parallel — `asyncio.gather` with per-adapter exception isolation.
* Optional streaming — caller passes `on_offer` and we wire it through.
* Optional cache — passed in from the FastAPI dependency layer; if `None`,
  the orchestrator runs cache-less (e.g. in tests).
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import structlog

from pricepulse.analytics.scoring import best_deal_score
from pricepulse.antibot.cascade import CascadeRouter
from pricepulse.antibot.ratelimit import RateLimiter
from pricepulse.cache.redis_cache import RedisCache
from pricepulse.config import get_settings
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer, RankedOffer, SourceGroup
from pricepulse.enrichment.normalize import normalize_query
from pricepulse.scrapers.base import ScrapeResult, ScraperProtocol
from pricepulse.scrapers.ozon import OzonScraper
from pricepulse.scrapers.runet import RunetScraper
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
        limiter: RateLimiter | None = None,
        cascade: CascadeRouter | None = None,
    ) -> None:
        settings = get_settings()
        # Default registry. RunetScraper is the 4th source — self-hosted
        # SearXNG + JSON-LD scrape, no external APIs (methodology compliance).
        self._registry: dict[SourceKind, ScraperProtocol] = adapters or {
            SourceKind.WB: WildberriesScraper(),
            SourceKind.OZON: OzonScraper(),
            SourceKind.YA_MARKET: YandexMarketScraper(),
            SourceKind.RUNET: RunetScraper(),
        }
        self._cache = cache
        # Anti-bot L0 — token-bucket rate limiter. Defaults to a process-local
        # bucket; the API layer injects a Redis-backed one so every worker
        # shares one budget. See antibot/ratelimit.py.
        self._limiter = limiter or RateLimiter(None)
        self._rpm: dict[SourceKind, int] = {
            SourceKind.WB: settings.wb_rpm,
            SourceKind.OZON: settings.ozon_rpm,
            SourceKind.YA_MARKET: settings.yandex_market_rpm,
            SourceKind.RUNET: settings.runet_rpm,
        }
        # Per-source cascade state — escalates the anti-bot layer after
        # repeated blocks within a window. See antibot/cascade.py.
        self._cascade = cascade or CascadeRouter()

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
        *,
        nofix: bool = False,
    ) -> tuple[NormalizedQuery, list[SourceGroup], list[RankedOffer]]:
        normalized = await normalize_query(query, fix=not nofix, cache=self._cache)
        adapters = self._pick(sources)
        results = await asyncio.gather(
            *[self._safe_call(a, normalized, max_per_source) for a in adapters]
        )
        groups = [_to_group(r) for r in results]
        top_deals = _rank_top_deals(groups, top_k=10)
        return normalized, groups, top_deals

    async def stream(
        self,
        query: str,
        max_per_source: int,
        sources: list[SourceKind] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yields SSE-shaped events as adapters report offers."""
        started = time.perf_counter()
        normalized = await normalize_query(query, cache=self._cache)
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
            group = _to_group(result)
            await queue.put((
                "source_finished",
                {
                    "source": adapter.source.value,
                    "count": group.count,
                    "min_price": str(group.min_price) if group.min_price is not None else None,
                    "avg_price": str(group.avg_price) if group.avg_price is not None else None,
                    "median_price": str(group.median_price)
                    if group.median_price is not None else None,
                    "error": group.error,
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

        # L0 anti-bot — wait for a rate-limit token before hitting the source.
        await self._limiter.acquire(
            adapter.source.value, self._rpm.get(adapter.source, 30),
        )

        try:
            result = await adapter.search(normalized, limit=limit, on_offer=on_offer)
        except Exception as exc:  # never propagate — isolate sources
            log.warning(
                "orchestrator.adapter_crash",
                source=adapter.source.value, error=str(exc),
            )
            return ScrapeResult(source=adapter.source, offers=[], error=str(exc))

        # Synonym retry — an empty (not blocked) result is often just a
        # vocabulary mismatch; retry once with the top synonym variant.
        if (
            not result.offers
            and not result.error
            and normalized.alternates
        ):
            alt = NormalizedQuery(raw=normalized.raw, normalized=normalized.alternates[0])
            log.info(
                "orchestrator.synonym_retry",
                source=adapter.source.value, alt=alt.normalized,
            )
            try:
                alt_result = await adapter.search(alt, limit=limit, on_offer=on_offer)
            except Exception as exc:
                log.warning("orchestrator.synonym_retry_failed", error=str(exc))
            else:
                if alt_result.offers:
                    result = alt_result

        # Feed the cascade router — repeated blocks escalate the anti-bot layer.
        layer = self._cascade.layer_for(adapter.source)
        ok = bool(result.offers) and not result.error
        self._cascade.record_outcome(adapter.source, layer, ok)
        if not ok:
            log.info(
                "orchestrator.source_blocked",
                source=adapter.source.value, layer=int(layer), error=result.error,
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


_CENTS = Decimal("0.01")


def _to_group(result: ScrapeResult) -> SourceGroup:
    offers = result.offers
    prices = sorted(o.price for o in offers)
    if prices:
        total = sum(prices, start=Decimal(0))
        min_price: Decimal | None = prices[0]
        avg_price: Decimal | None = (total / len(prices)).quantize(_CENTS)
        median_price: Decimal | None = statistics.median(prices).quantize(_CENTS)
    else:
        min_price = avg_price = median_price = None
    return SourceGroup(
        source=result.source,
        count=len(offers),
        min_price=min_price,
        avg_price=avg_price,
        median_price=median_price,
        offers=offers,
        error=result.error,
    )


def _rank_top_deals(groups: list[SourceGroup], top_k: int = 10) -> list[RankedOffer]:
    """Best-Deal Score across ALL sources, returns top-K as RankedOffer.

    Price population = every offer in the result set, so price_z is computed
    cross-source — that lets a cheap Megamarket offer outrank a more famous WB
    one on identical specs.
    """
    all_offers: list[ProductOffer] = []
    for g in groups:
        all_offers.extend(g.offers)
    if not all_offers:
        return []
    prices = [o.price for o in all_offers]
    ranked: list[tuple[float, ProductOffer]] = []
    for o in all_offers:
        score = best_deal_score(
            price=o.price,
            rating=o.rating or 0.0,
            reviews_count=int(o.characteristics.get("feedbacks") or 0),
            price_population=prices,
        )
        ranked.append((score, o))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [
        RankedOffer(offer=o, score=round(s, 4), rank=i + 1)
        for i, (s, o) in enumerate(ranked[:top_k])
    ]
