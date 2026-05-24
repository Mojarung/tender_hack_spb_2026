"""Search orchestrator: fan-out, source isolation, optional SSE streaming."""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import structlog

from pricepulse.analytics.scoring import best_deal_score, composite_rank_score
from pricepulse.antibot.cascade import CascadeRouter
from pricepulse.antibot.ratelimit import RateLimiter
from pricepulse.cache.redis_cache import RedisCache
from pricepulse.config import get_settings
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import (
    NormalizedQuery,
    ProductAttributes,
    ProductOffer,
    QueryClarification,
    RankedOffer,
    SourceGroup,
)
from pricepulse.enrichment.attributes import (
    extract_offer_attributes,
    extract_query_attributes,
    is_attribute_conflict,
    merge_attributes,
    relevance_breakdown,
)
from pricepulse.enrichment.normalize import normalize_query
from pricepulse.enrichment.query_clarification import check_and_clarify_query
from pricepulse.scrapers.base import ScrapeResult, ScraperProtocol

# TEMP: imports kept (commented in registry below) so reverting is one
# uncomment, not a re-add. See _registry construction for the toggle.
from pricepulse.scrapers.ozon import OzonScraper
from pricepulse.scrapers.runet import RunetScraper  # noqa: F401
from pricepulse.scrapers.wb import WildberriesScraper
from pricepulse.scrapers.yandex_market import YandexMarketScraper

log = structlog.get_logger(__name__)

_CACHE_TTL: dict[SourceKind, int] = {
    SourceKind.WB: 15 * 60,
    SourceKind.OZON: 15 * 60,
    SourceKind.YA_MARKET: 15 * 60,
    SourceKind.RUNET: 15 * 60,
}
_CENTS = Decimal("0.01")
_REVIEW_KEYS: tuple[str, ...] = ("feedbacks", "reviews", "rating_count")

_STALE_CACHE_TTL: dict[SourceKind, int] = {
    SourceKind.WB: 6 * 60 * 60,
    SourceKind.OZON: 60 * 60,
    SourceKind.YA_MARKET: 60 * 60,
    SourceKind.RUNET: 60 * 60,
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
        self._registry: dict[SourceKind, ScraperProtocol] = adapters or {
            SourceKind.WB: WildberriesScraper(),
            SourceKind.OZON: OzonScraper(),
            SourceKind.YA_MARKET: YandexMarketScraper(),
        }
        self._cache = cache
        self._limiter = limiter or RateLimiter(None)
        self._rpm: dict[SourceKind, int] = {
            SourceKind.WB: settings.wb_rpm,
            SourceKind.OZON: settings.ozon_rpm,
            SourceKind.YA_MARKET: settings.yandex_market_rpm,
            SourceKind.RUNET: settings.runet_rpm,
        }
        self._inflight: dict[str, asyncio.Task[ScrapeResult]] = {}
        self._inflight_lock = asyncio.Lock()
        self._cascade = cascade or CascadeRouter()

    def _pick(self, sources: list[SourceKind] | None) -> list[ScraperProtocol]:
        if not sources:
            return list(self._registry.values())
        return [self._registry[s] for s in sources if s in self._registry]

    async def run(
        self,
        query: str,
        max_per_source: int,
        sources: list[SourceKind] | None = None,
        *,
        region_id: int = 213,
        nofix: bool = False,
    ) -> tuple[NormalizedQuery, list[SourceGroup], list[RankedOffer], QueryClarification | None]:
        clarification_task = asyncio.create_task(check_and_clarify_query(query))
        normalized = await normalize_query(query, fix=not nofix, cache=self._cache)
        query_attrs = await self._query_attributes(normalized.normalized or normalized.raw)
        normalized = normalized.model_copy(update={"attributes": query_attrs})
        results = await asyncio.gather(
            *[
                self._safe_call(adapter, normalized, max_per_source, region_id=region_id)
                for adapter in self._pick(sources)
            ]
        )
        groups = [_to_group(r) for r in results]
        top_deals = _rank_top_deals(groups, query_attrs=query_attrs, top_k=10)
        try:
            clarification = await clarification_task
        except Exception:
            clarification = None
        return normalized, groups, top_deals, clarification

    async def stream(
        self,
        query: str,
        max_per_source: int,
        sources: list[SourceKind] | None = None,
        region_id: int = 213,
        *,
        nofix: bool = False,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        started = time.perf_counter()
        clarification_task = asyncio.create_task(check_and_clarify_query(query))

        normalized = await normalize_query(query, fix=not nofix, cache=self._cache)
        query_attrs = await self._query_attributes(normalized.normalized or normalized.raw)
        normalized = normalized.model_copy(update={"attributes": query_attrs})
        yield "query_normalized", normalized.model_dump()

        try:
            clarification = await clarification_task
            yield "query_clarified", clarification.model_dump(mode="json")
        except Exception as exc:
            log.warning("orchestrator.clarification_stream_failed", error=str(exc))

        adapters = self._pick(sources)
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        groups: dict[SourceKind, SourceGroup] = {}

        async def _drive(adapter: ScraperProtocol) -> None:
            await queue.put(("source_started", {"source": adapter.source.value}))

            async def on_offer(offer: ProductOffer) -> None:
                await queue.put(("offer", {"source": adapter.source.value, "offer": offer.model_dump(mode="json")}))

            result = await self._safe_call(
                adapter,
                normalized,
                max_per_source,
                on_offer=on_offer,
                region_id=region_id,
            )
            group = _to_group(result)
            groups[adapter.source] = group
            await queue.put((
                "source_finished",
                {
                    "source": adapter.source.value,
                    "count": group.count,
                    "min_price": str(group.min_price) if group.min_price is not None else None,
                    "avg_price": str(group.avg_price) if group.avg_price is not None else None,
                    "median_price": str(group.median_price) if group.median_price is not None else None,
                    "error": group.error,
                    "cached": result.cached,
                },
            ))

        async def _drain() -> None:
            await asyncio.gather(*[_drive(a) for a in adapters])
            top_deals = _rank_top_deals(list(groups.values()), query_attrs=query_attrs, top_k=10)
            await queue.put(("top_deals", {"top_deals": [d.model_dump(mode="json") for d in top_deals]}))
            await queue.put(None)

        drainer = asyncio.create_task(_drain())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            drainer.cancel()
            try:
                await drainer
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.warning("orchestrator.drainer_failed", error=str(exc))
        yield "done", {"took_ms": int((time.perf_counter() - started) * 1000)}

    async def _query_attributes(self, text: str) -> ProductAttributes:
        return extract_query_attributes(text)

    async def _safe_call(
        self,
        adapter: ScraperProtocol,
        normalized: NormalizedQuery,
        limit: int,
        on_offer=None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        cache_key = f"cache:{adapter.source.value}:{region_id}:{normalized.normalized}:{limit}"
        if self._cache is not None:
            try:
                cached = await self._cache.get(cache_key)
            except Exception as exc:
                log.debug("orchestrator.cache_get_failed", error=str(exc))
                cached = None
            if cached:
                offers = [
                    _enrich_offer(ProductOffer.model_validate(o).model_copy(update={"cached": True}))
                    for o in cached.get("offers", [])
                ]
                if on_offer is not None:
                    for offer in offers:
                        await on_offer(offer)
                return ScrapeResult(source=adapter.source, offers=offers, cached=True)

        async with self._inflight_lock:
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(self._fetch_source(adapter, normalized, limit, on_offer, region_id))
                self._inflight[cache_key] = task
                owner = True
            else:
                owner = False
        try:
            result = await task
        finally:
            if owner:
                async with self._inflight_lock:
                    self._inflight.pop(cache_key, None)
        if not owner:
            return result

        if result.error and adapter.source == SourceKind.WB and self._cache is not None:
            stale = await self._get_stale_result(cache_key, adapter.source, on_offer)
            if stale is not None:
                log.info(
                    "orchestrator.stale_cache_served",
                    source=adapter.source.value,
                    reason=result.error,
                )
                return stale

        # Populate cache on success — wrapped because Redis being down
        # is never a fatal condition for a successful search.
        if self._cache is not None and result.offers and not result.error:
            ttl = _CACHE_TTL.get(adapter.source, 3600)
            stale_ttl = _STALE_CACHE_TTL.get(adapter.source, ttl)
            payload = {"offers": [o.model_dump(mode="json") for o in result.offers]}
            try:
                await self._cache.set(cache_key, payload, ttl_seconds=ttl)
                await self._cache.set_stale(cache_key, payload, ttl_seconds=stale_ttl)
            except Exception as exc:
                log.debug("orchestrator.cache_set_failed", error=str(exc))
        return result

    async def _fetch_source(
        self,
        adapter: ScraperProtocol,
        normalized: NormalizedQuery,
        limit: int,
        on_offer=None,
        region_id: int = 213,
    ) -> ScrapeResult:
        await self._limiter.acquire(adapter.source.value, self._rpm.get(adapter.source, 30))

        async def enriched_on_offer(offer: ProductOffer) -> None:
            if on_offer is not None:
                await on_offer(_enrich_offer(offer))

        try:
            result = await adapter.search(
                normalized,
                limit=limit,
                on_offer=enriched_on_offer if on_offer is not None else None,
                region_id=region_id,
            )
        except Exception as exc:  # never propagate — isolate sources
            # `str(exc)` is empty for many exception types (e.g. some
            # nodriver/asyncio errors). `repr` keeps the type name.
            log.warning(
                "orchestrator.adapter_crash",
                source=adapter.source.value,
                error=repr(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return ScrapeResult(
                source=adapter.source,
                offers=[],
                error=f"{type(exc).__name__}: {exc}".strip(": "),
            )

        if not result.offers and not result.error and normalized.alternates:
            alt = NormalizedQuery(
                raw=normalized.raw,
                normalized=normalized.alternates[0],
                attributes=normalized.attributes,
            )
            log.info("orchestrator.synonym_retry", source=adapter.source.value, alt=alt.normalized)
            try:
                alt_result = await adapter.search(
                    alt,
                    limit=limit,
                    on_offer=enriched_on_offer if on_offer is not None else None,
                    region_id=region_id,
                )
            except Exception as exc:
                log.warning("orchestrator.synonym_retry_failed", error=str(exc))
            else:
                if alt_result.offers:
                    result = alt_result

        result = _enrich_result(result)
        layer = self._cascade.layer_for(adapter.source)
        ok = bool(result.offers) and not result.error
        self._cascade.record_outcome(adapter.source, layer, ok)
        if not ok:
            log.info(
                "orchestrator.source_blocked",
                source=adapter.source.value,
                layer=int(layer),
                error=result.error,
            )
        return result

    async def _get_stale_result(
        self,
        cache_key: str,
        source: SourceKind,
        on_offer=None,
    ) -> ScrapeResult | None:
        try:
            stale = await self._cache.get_stale(cache_key) if self._cache is not None else None
        except Exception as exc:
            log.debug("orchestrator.stale_cache_get_failed", error=str(exc))
            return None
        if not stale:
            return None
        offers = [
            _enrich_offer(ProductOffer.model_validate(o).model_copy(update={"cached": True}))
            for o in stale.get("offers", [])
        ]
        if on_offer is not None:
            for offer in offers:
                await on_offer(offer)
        return ScrapeResult(source=source, offers=offers, cached=True)


def _reviews_count(chars: dict[str, str]) -> int:
    for key in _REVIEW_KEYS:
        value = chars.get(key)
        if not value:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _to_group(result: ScrapeResult) -> SourceGroup:
    prices = sorted(o.price for o in result.offers)
    if prices:
        total = sum(prices, start=Decimal(0))
        min_price: Decimal | None = prices[0]
        avg_price: Decimal | None = (total / len(prices)).quantize(_CENTS)
        median_price: Decimal | None = statistics.median(prices).quantize(_CENTS)
    else:
        min_price = avg_price = median_price = None
    return SourceGroup(
        source=result.source,
        count=len(result.offers),
        min_price=min_price,
        avg_price=avg_price,
        median_price=median_price,
        offers=result.offers,
        error=result.error,
    )


def _rank_top_deals(
    groups: list[SourceGroup],
    *,
    query_attrs: ProductAttributes | None = None,
    top_k: int = 10,
) -> list[RankedOffer]:
    all_offers = [offer for group in groups for offer in group.offers]
    if not all_offers:
        return []

    candidates = all_offers
    if query_attrs is not None and query_attrs.confidence >= 0.3:
        filtered = [offer for offer in all_offers if not is_attribute_conflict(query_attrs, offer.attributes)[0]]
        if len(filtered) >= max(3, top_k // 2):
            candidates = filtered

    prices = [offer.price for offer in candidates]
    ranked: list[tuple[float, float, float, ProductOffer, dict[str, list[str] | float]]] = []
    for offer in candidates:
        reviews_count = (
            offer.reviews_count
            if offer.reviews_count is not None
            else _reviews_count(offer.characteristics)
        )
        deal = best_deal_score(
            price=offer.price,
            rating=offer.rating or 0.0,
            reviews_count=reviews_count,
            price_population=prices,
            delivery_days=_delivery_days(offer),
        )
        breakdown = relevance_breakdown(query_attrs, offer.attributes)
        relevance = float(breakdown["score"])
        qconf = query_attrs.confidence if query_attrs else 0.0
        ranked.append((composite_rank_score(deal, relevance, qconf), deal, relevance, offer, breakdown))

    ranked.sort(key=lambda item: (-item[0], float(item[3].price), -(item[3].rating or 0.0)))
    return [
        RankedOffer(
            offer=offer,
            score=round(composite, 4),
            rank=index + 1,
            deal_score=round(deal, 4),
            relevance_score=round(relevance, 4),
            match_signals=breakdown.get("matched", []),
            mismatch_signals=breakdown.get("mismatched", []),
            unknown_signals=breakdown.get("unknown", []),
        )
        for index, (composite, deal, relevance, offer, breakdown) in enumerate(ranked[:top_k])
    ]


def _delivery_days(offer: ProductOffer) -> int:
    if offer.delivery is None or offer.delivery.eta_max_hours is None:
        return 3
    return max(1, (offer.delivery.eta_max_hours + 23) // 24)


def _enrich_offer(offer: ProductOffer) -> ProductOffer:
    extracted = extract_offer_attributes(offer)
    attrs = merge_attributes(offer.attributes, extracted)
    return offer.model_copy(update={"attributes": attrs})


def _enrich_result(result: ScrapeResult) -> ScrapeResult:
    return ScrapeResult(
        source=result.source,
        offers=[_enrich_offer(o) for o in result.offers],
        error=result.error,
        cached=result.cached,
    )
