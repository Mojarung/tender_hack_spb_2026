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
from statistics import median
from typing import Any

import structlog

from pricepulse.analytics.scoring import best_deal_score, composite_rank_score
from pricepulse.antibot.cascade import CascadeRouter
from pricepulse.antibot.ratelimit import RateLimiter
from pricepulse.cache.redis_cache import RedisCache
from pricepulse.config import get_settings
from pricepulse.core.features import FeatureFlags
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import (
    NormalizedQuery,
    ProductAttributes,
    ProductOffer,
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
from pricepulse.scrapers.base import ScrapeResult, ScraperProtocol
from pricepulse.scrapers.megamarket import MegamarketScraper
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
        runet_fallback: ScraperProtocol | None = None,
        limiter: RateLimiter | None = None,
        cascade: CascadeRouter | None = None,
    ) -> None:
        settings = get_settings()
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
        self._cascade = cascade or CascadeRouter(FeatureFlags.from_settings(settings))
        self._settings = settings

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
        city: str | None = None,
    ) -> tuple[NormalizedQuery, list[SourceGroup], list[RankedOffer]]:
        normalized = await normalize_query(query, fix=not nofix)
        query_attrs = await self._query_attributes(normalized.normalized or normalized.raw)
        normalized = normalized.model_copy(update={"attributes": query_attrs})
        adapters = self._with_location(self._pick(sources), city=city)
        cache_suffix = _cache_suffix(city)
        results = await asyncio.gather(
            *[
                self._safe_call(
                    a,
                    normalized,
                    max_per_source,
                    cache_suffix=cache_suffix,
                )
                for a in adapters
            ]
        )
        groups = [_to_group(r) for r in results]
        top_deals = _rank_top_deals(groups, query_attrs=query_attrs, top_k=10)
        return normalized, groups, top_deals

    async def stream(
        self,
        query: str,
        max_per_source: int,
        sources: list[SourceKind] | None = None,
        city: str | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yields SSE-shaped events as adapters report offers."""
        started = time.perf_counter()
        normalized = await normalize_query(query)
        query_attrs = await self._query_attributes(normalized.normalized or normalized.raw)
        normalized = normalized.model_copy(update={"attributes": query_attrs})
        yield "query_normalized", normalized.model_dump()

        adapters = self._with_location(self._pick(sources), city=city)
        cache_suffix = _cache_suffix(city)
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def _drive(adapter: ScraperProtocol) -> None:
            await queue.put(("source_started", {"source": adapter.source.value}))

            async def on_offer(offer: ProductOffer) -> None:
                await queue.put((
                    "offer",
                    {"source": adapter.source.value, "offer": offer.model_dump(mode="json")},
                ))

            result = await self._safe_call(
                adapter,
                normalized,
                max_per_source,
                on_offer=on_offer,
                cache_suffix=cache_suffix,
            )
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

    def _with_location(
        self,
        adapters: list[ScraperProtocol],
        *,
        city: str | None,
    ) -> list[ScraperProtocol]:
        if not city:
            return adapters
        out: list[ScraperProtocol] = []
        for adapter in adapters:
            if isinstance(adapter, WildberriesScraper):
                out.append(WildberriesScraper(city=city))
            else:
                out.append(adapter)
        return out

    async def _query_attributes(self, text: str) -> ProductAttributes:
        return extract_query_attributes(text)

    # ──────────────────────────────────────── internals ──────────────────────

    async def _safe_call(
        self,
        adapter: ScraperProtocol,
        normalized: NormalizedQuery,
        limit: int,
        on_offer=None,
        *,
        cache_suffix: str = "",
    ) -> ScrapeResult:
        cache_key = f"cache:{adapter.source.value}:{normalized.normalized}:{limit}{cache_suffix}"
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached:
                offers = [
                    _enrich_offer(
                        ProductOffer.model_validate(o).model_copy(update={"cached": True}),
                    )
                    for o in cached.get("offers", [])
                ]
                if on_offer is not None:
                    for o in offers:
                        await on_offer(o)
                return ScrapeResult(source=adapter.source, offers=offers, cached=True)

        # L0 anti-bot — wait for a rate-limit token before hitting the source.
        await self._limiter.acquire(
            adapter.source.value, self._rpm.get(adapter.source, 30),
        )

        async def enriched_on_offer(offer: ProductOffer) -> None:
            if on_offer is not None:
                await on_offer(_enrich_offer(offer))

        try:
            result = await adapter.search(
                normalized,
                limit=limit,
                on_offer=enriched_on_offer if on_offer is not None else None,
            )
        except Exception as exc:  # never propagate — isolate sources
            log.warning("orchestrator.adapter_crash",
                        source=adapter.source.value, error=str(exc))
            # Runet has a deterministic fallback (Megamarket)
            if adapter.source == SourceKind.RUNET:
                try:
                    result = await self._runet_fallback.search(
                        normalized,
                        limit=limit,
                        on_offer=enriched_on_offer if on_offer is not None else None,
                    )
                except Exception as fb_exc:
                    return ScrapeResult(
                        source=adapter.source, offers=[],
                        error=f"runet+fallback failed: {fb_exc}",
                    )
            else:
                return ScrapeResult(source=adapter.source, offers=[], error=str(exc))

        # If primary RunetScraper returned nothing, try Megamarket
        if adapter.source == SourceKind.RUNET and not result.offers and not result.error:
            result = await self._runet_fallback.search(
                normalized,
                limit=limit,
                on_offer=enriched_on_offer if on_offer is not None else None,
            )

        result = _enrich_result(result)

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


def _to_group(result: ScrapeResult) -> SourceGroup:
    offers = result.offers
    min_price = min((o.price for o in offers), default=None)
    median_price = median([o.price for o in offers]) if offers else None
    return SourceGroup(
        source=result.source,
        count=len(offers),
        min_price=min_price,
        median_price=median_price,
        offers=offers,
        error=result.error,
    )


def _rank_top_deals(
    groups: list[SourceGroup],
    *,
    query_attrs: ProductAttributes | None = None,
    top_k: int = 10,
) -> list[RankedOffer]:
    """Safe ranking pipeline:

    1. Collect all offers from every source group.
    2. Hard-filter out confirmed attribute conflicts (only when query_conf is
       high enough; with a safety rollback if the filter is too aggressive).
    3. Compute price_population from the FILTERED set so the z-score for the
       deal_score isn't pulled by accessory outliers.
    4. For each surviving offer: deal_score, relevance breakdown, composite.
    5. Tie-break sort by (composite desc, price asc, rating desc).
    6. Return top-K as RankedOffer carrying explain signals.
    """
    all_offers: list[ProductOffer] = []
    for g in groups:
        all_offers.extend(g.offers)
    if not all_offers:
        return []

    # ── 2. Hard filter ────────────────────────────────────────────────────────
    candidates = all_offers
    if query_attrs is not None and query_attrs.confidence >= 0.3:
        filtered = [
            o for o in all_offers
            if not is_attribute_conflict(query_attrs, o.attributes)[0]
        ]
        # Safety rollback: don't shrink below a meaningful set
        if len(filtered) >= max(3, top_k // 2):
            candidates = filtered

    # ── 3. Price population from filtered set ─────────────────────────────────
    prices = [o.price for o in candidates]
    ranked: list[tuple[float, float, float, ProductOffer, dict]] = []
    for o in candidates:
        delivery_days = _delivery_days(o)
        deal = best_deal_score(
            price=o.price,
            rating=o.rating or 0.0,
            reviews_count=int(o.characteristics.get("feedbacks") or 0),
            price_population=prices,
            delivery_days=delivery_days,
        )
        breakdown = relevance_breakdown(query_attrs, o.attributes)
        relevance = float(breakdown["score"])
        qconf = query_attrs.confidence if query_attrs else 0.0
        composite = composite_rank_score(deal, relevance, qconf)
        ranked.append((composite, deal, relevance, o, breakdown))

    # ── 5. Sort: composite desc, price asc, rating desc ───────────────────────
    ranked.sort(
        key=lambda x: (-x[0], float(x[3].price), -(x[3].rating or 0.0)),
    )

    return [
        RankedOffer(
            offer=o,
            score=round(comp, 4),
            rank=i + 1,
            deal_score=round(deal, 4),
            relevance_score=round(rel, 4),
            match_signals=breakdown.get("matched", []),
            mismatch_signals=breakdown.get("mismatched", []),
            unknown_signals=breakdown.get("unknown", []),
        )
        for i, (comp, deal, rel, o, breakdown) in enumerate(ranked[:top_k])
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


def _cache_suffix(city: str | None) -> str:
    if not city:
        return ""
    return f":city={city.strip().lower()}"
