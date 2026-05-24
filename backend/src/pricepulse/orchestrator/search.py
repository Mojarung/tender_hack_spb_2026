"""Search orchestrator: fan-out, source isolation, optional SSE streaming."""

from __future__ import annotations

import asyncio
import re
import statistics
import time
import unicodedata
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import structlog

from pricepulse.analytics.regional_pricing import adjust_offer_for_region
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
from pricepulse.enrichment.normalization import canonicalize_characteristics
from pricepulse.enrichment.normalize import normalize_query
from pricepulse.enrichment.query_clarification import check_and_clarify_query
from pricepulse.enrichment.reranker import HttpReranker, RerankerProtocol
from pricepulse.scrapers.base import ScrapeResult, ScraperProtocol

# TEMP: imports kept (commented in registry below) so reverting is one
# uncomment, not a re-add. See _registry construction for the toggle.
from pricepulse.scrapers.ozon import OzonScraper
from pricepulse.scrapers.runet import RunetScraper
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
_GENERIC_PUNCT = re.compile(r"[^\w\s/+.-]", flags=re.UNICODE)
_GENERIC_SPACES = re.compile(r"\s+")
_GENERIC_STOPWORDS = {
    "для", "без", "или", "это", "как", "что", "при", "под", "над", "the",
    "and", "with", "from", "на", "в", "с", "и", "a", "an",
}
_GENERIC_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:gb|гб|tb|тб|см|cm|мм|mm|л|l|hz|гц|вт|w|мл|ml|шт|pcs)?\b")

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
        reranker: RerankerProtocol | None = None,
    ) -> None:
        settings = get_settings()
        self._registry: dict[SourceKind, ScraperProtocol] = adapters or {
            SourceKind.WB: WildberriesScraper(),
            SourceKind.OZON: OzonScraper(),
            SourceKind.YA_MARKET: YandexMarketScraper(),
            # RunetScraper fans out internally to Google Shopping +
            # Yandex SERP and merges the result under one banner.
            SourceKind.RUNET: RunetScraper(),
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
        self._reranker = reranker or (
            HttpReranker(base_url=settings.reranker_url, timeout_s=settings.reranker_timeout_s)
            if settings.reranker_enabled
            else None
        )
        self._reranker_top_n = settings.reranker_top_n
        self._reranker_weight = settings.reranker_weight

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
        top_deals = await _rank_top_deals(
            groups,
            query_text=normalized.normalized or normalized.raw,
            query_attrs=query_attrs,
            reranker=self._reranker,
            reranker_top_n=self._reranker_top_n,
            reranker_weight=self._reranker_weight,
            top_k=10,
        )
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

        async def _emit_top_deals(*, with_reranker: bool) -> None:
            all_offer_count = sum(g.count for g in groups.values())
            if all_offer_count == 0:
                return
            top_deals = await _rank_top_deals(
                list(groups.values()),
                query_text=normalized.normalized or normalized.raw,
                query_attrs=query_attrs,
                reranker=self._reranker if with_reranker else None,
                reranker_top_n=self._reranker_top_n,
                reranker_weight=self._reranker_weight,
                top_k=max(10, all_offer_count),  # ранжируем все офферы — фронт сортирует по relevance
            )
            await queue.put(("top_deals", {"top_deals": [d.model_dump(mode="json") for d in top_deals]}))

        async def _drain() -> None:
            # Стримим top_deals инкрементально после каждого источника, чтобы
            # фронт мог сортировать офферы по релевантности «вживую», а не
            # ждать конца fan-out. Реранкер запускаем только в финальной волне
            # — он дорогой и шумит для частичных результатов.
            pending = [asyncio.create_task(_drive(a)) for a in adapters]
            for task in asyncio.as_completed(pending):
                await task
                await _emit_top_deals(with_reranker=False)
            await _emit_top_deals(with_reranker=True)
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

        # Регион-адъюст для WB/Ozon (они игнорируют region_id) + enrich
        # с атрибутами/каноническими характеристиками. Оборачиваем
        # on_offer чтобы SSE сразу нёс адъюстнутые цены без флика.
        wrapped_on_offer = None
        if on_offer is not None:
            if region_id != 213:
                async def _enrich_and_adjust(offer: ProductOffer, _cb=on_offer) -> None:
                    await _cb(adjust_offer_for_region(_enrich_offer(offer), region_id))
                wrapped_on_offer = _enrich_and_adjust
            else:
                async def _enriched(offer: ProductOffer, _cb=on_offer) -> None:
                    await _cb(_enrich_offer(offer))
                wrapped_on_offer = _enriched

        try:
            result = await adapter.search(
                normalized,
                limit=limit,
                on_offer=wrapped_on_offer,
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
                    on_offer=wrapped_on_offer,
                    region_id=region_id,
                )
            except Exception as exc:
                log.warning("orchestrator.synonym_retry_failed", error=str(exc))
            else:
                if alt_result.offers:
                    result = alt_result

        result = _enrich_result(result)

        # Regional pricing — применяем к финальному списку, чтобы кэш
        # и min/avg/median считались с адъюстнутыми ценами.
        if region_id != 213 and result.offers:
            result = ScrapeResult(
                source=result.source,
                offers=[adjust_offer_for_region(o, region_id) for o in result.offers],
                error=result.error,
                cached=result.cached,
            )

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


async def _rank_top_deals(
    groups: list[SourceGroup],
    *,
    query_text: str = "",
    query_attrs: ProductAttributes | None = None,
    reranker: RerankerProtocol | None = None,
    reranker_top_n: int = 20,
    reranker_weight: float = 0.25,
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
        breakdown = _combined_relevance_breakdown(query_text, query_attrs, offer)
        relevance = float(breakdown["score"])
        qconf = query_attrs.confidence if query_attrs else 0.0
        ranked.append((composite_rank_score(deal, relevance, qconf), deal, relevance, offer, breakdown))

    # Дефолтная сортировка — только по релевантности (процент соответствия).
    # Тайбрейкеры: composite (учитывает deal), затем цена, затем рейтинг.
    ranked.sort(key=lambda item: (-item[2], -item[0], float(item[3].price), -(item[3].rating or 0.0)))
    top = [
        RankedOffer(
            offer=offer,
            score=round(composite, 4),
            rank=index + 1,
            deal_score=round(deal, 4),
            relevance_score=round(relevance, 4),
            relevance_percent=_percent(relevance),
            selection_reasons=_selection_reasons(
                relevance=relevance,
                deal=deal,
                matched=breakdown.get("matched", []),
                mismatched=breakdown.get("mismatched", []),
            ),
            match_signals=breakdown.get("matched", []),
            mismatch_signals=breakdown.get("mismatched", []),
            unknown_signals=breakdown.get("unknown", []),
        )
        for index, (composite, deal, relevance, offer, breakdown) in enumerate(ranked[:top_k])
    ]
    if reranker is None or not query_text.strip() or not top:
        return top
    return await _apply_reranker(
        top,
        query_text=query_text,
        reranker=reranker,
        top_n=reranker_top_n,
        weight=reranker_weight,
    )


async def _apply_reranker(
    ranked: list[RankedOffer],
    *,
    query_text: str,
    reranker: RerankerProtocol,
    top_n: int,
    weight: float,
) -> list[RankedOffer]:
    candidates = ranked[: max(1, min(top_n, len(ranked)))]
    documents = [_rerank_document(item.offer) for item in candidates]
    try:
        results = await reranker.rerank(query_text, documents, top_n=len(documents))
    except Exception as exc:  # noqa: BLE001 - reranker is optional; search must continue.
        log.warning("reranker.failed", error=str(exc))
        return ranked
    if not results:
        return ranked

    rerank_by_index = {result.index: max(0.0, min(1.0, result.score)) for result in results}
    blend_weight = max(0.0, min(1.0, weight))
    reranked: list[RankedOffer] = []
    for index, item in enumerate(candidates):
        rerank_score = rerank_by_index.get(index)
        if rerank_score is None:
            reranked.append(item)
            continue
        blended = (item.score * (1.0 - blend_weight)) + ((rerank_score - 0.5) * blend_weight)
        signals = list(dict.fromkeys([*item.match_signals, "reranker"]))
        reranked.append(
            item.model_copy(
                update={
                    "score": round(blended, 4),
                    "rerank_score": round(rerank_score, 4),
                    "selection_reasons": _selection_reasons(
                        relevance=item.relevance_score,
                        deal=item.deal_score,
                        matched=signals,
                        mismatched=item.mismatch_signals,
                        rerank_score=rerank_score,
                    ),
                    "match_signals": signals,
                }
            )
        )

    reranked.sort(key=lambda item: (-item.score, float(item.offer.price), -(item.offer.rating or 0.0)))
    tail = ranked[len(candidates):]
    output = [item.model_copy(update={"rank": index + 1}) for index, item in enumerate([*reranked, *tail])]
    return output


def _rerank_document(offer: ProductOffer) -> str:
    parts = [offer.name]
    if offer.attributes is not None:
        attr_data = offer.attributes.model_dump(exclude_none=True, exclude={"raw", "extra", "confidence"})
        parts.extend(f"{key}: {value}" for key, value in attr_data.items())
    if offer.canonical_characteristics is not None:
        parts.extend(
            f"{attr.key}: {attr.value}"
            for attr in offer.canonical_characteristics.attributes.values()
        )
    elif offer.characteristics:
        parts.extend(f"{key}: {value}" for key, value in offer.characteristics.items() if value)
    return " | ".join(str(part) for part in parts if part)[:1800]


def _percent(score: float) -> int:
    return max(0, min(100, int(round(score * 100))))


_SIGNAL_LABELS: dict[str, str] = {
    "category": "категория",
    "brand": "бренд",
    "model": "модель",
    "color": "цвет",
    "storage_gb": "память",
    "ram_gb": "ОЗУ",
    "paper_format": "формат",
    "density_gm2": "плотность",
    "sheets_count": "листов в пачке",
    "pack_count": "количество",
    "device_type": "тип товара",
    "print_technology": "технология печати",
    "color_print": "цветность",
    "wifi": "Wi-Fi",
    "duplex": "двусторонняя печать",
    "staple_size": "размер скоб",
    "sheet_capacity": "листов за раз",
    "page_yield": "ресурс",
    "apparel_type": "тип одежды",
    "size": "размер",
    "gender": "пол",
    "material": "материал",
    "season": "сезон",
    "screen_size_inch": "диагональ",
    "refresh_rate_hz": "частота",
    "resolution": "разрешение",
    "matrix_type": "матрица",
    "security_level": "секретность",
    "reranker": "семантика",
}


def _signal_label(signal: str) -> str:
    if signal.startswith("text:"):
        return signal.removeprefix("text:")
    if signal.startswith("number:"):
        return signal.removeprefix("number:")
    return _SIGNAL_LABELS.get(signal, signal.replace("_", " "))


def _selection_reasons(
    *,
    relevance: float,
    deal: float,
    matched: list[str],
    mismatched: list[str],
    rerank_score: float | None = None,
) -> list[str]:
    reasons: list[str] = []
    clean_matched = [_signal_label(s) for s in matched if s != "category"]
    if clean_matched:
        reasons.append("Совпало: " + ", ".join(clean_matched[:4]))
    if rerank_score is not None and rerank_score >= 0.75:
        reasons.append("Реранкер подтвердил семантическую близость")
    if relevance >= 0.85:
        reasons.append("Высокое соответствие запросу")
    elif relevance >= 0.6:
        reasons.append("Подходит по ключевым признакам")
    if mismatched:
        reasons.append("Расхождение: " + ", ".join(_signal_label(s) for s in mismatched[:2]))
    if deal > 0.15:
        reasons.append("Хорошее соотношение цены и рейтинга")
    return reasons[:3]


def _combined_relevance_breakdown(
    query_text: str,
    query_attrs: ProductAttributes | None,
    offer: ProductOffer,
) -> dict[str, Any]:
    attribute = relevance_breakdown(query_attrs, offer.attributes)
    generic = _generic_relevance_breakdown(query_text, offer)

    # A confident category mismatch is a hard rejection; generic text overlap
    # must not resurrect printers for a cartridge query, etc.
    if "category" in attribute.get("mismatched", []):
        return attribute

    attr_score = float(attribute["score"])
    generic_score = float(generic["score"])
    weak_query = query_attrs is None or query_attrs.confidence < 0.3 or not query_attrs.category
    if weak_query:
        return generic if generic_score > 0 else attribute
    if generic_score > attr_score and attr_score < 0.75:
        return {
            "score": round((attr_score * 0.65) + (generic_score * 0.35), 4),
            "matched": list(dict.fromkeys([*attribute.get("matched", []), *generic.get("matched", [])])),
            "mismatched": attribute.get("mismatched", []),
            "unknown": attribute.get("unknown", []),
        }
    return attribute


def _generic_relevance_breakdown(query_text: str, offer: ProductOffer) -> dict[str, Any]:
    query_tokens = _generic_tokens(query_text)
    if not query_tokens:
        return {"score": 0.0, "matched": [], "mismatched": [], "unknown": []}

    offer_blob = _generic_offer_blob(offer)
    offer_tokens = _generic_tokens(offer_blob)
    matched_tokens = sorted(query_tokens & offer_tokens)
    token_score = len(matched_tokens) / len(query_tokens)

    query_numbers = _generic_numbers(query_text)
    number_score = 0.0
    matched_numbers: list[str] = []
    if query_numbers:
        offer_numbers = _generic_numbers(offer_blob)
        matched_numbers = sorted(query_numbers & offer_numbers)
        number_score = len(matched_numbers) / len(query_numbers)

    score = (token_score * 0.75) + (number_score * 0.25 if query_numbers else 0.0)
    if not query_numbers:
        score = token_score
    matched = [f"text:{token}" for token in matched_tokens[:8]]
    matched.extend(f"number:{num}" for num in matched_numbers[:5])
    mismatched = [f"text:{token}" for token in sorted(query_tokens - offer_tokens)[:5]]
    return {
        "score": round(score, 4),
        "matched": matched,
        "mismatched": mismatched,
        "unknown": [],
    }


def _generic_offer_blob(offer: ProductOffer) -> str:
    parts = [offer.name]
    parts.extend(f"{k} {v}" for k, v in offer.characteristics.items() if v)
    if offer.canonical_characteristics is not None:
        parts.extend(
            f"{attr.key} {attr.value}"
            for attr in offer.canonical_characteristics.attributes.values()
        )
    return " ".join(str(part) for part in parts if part)


def _generic_clean(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower().replace("ё", "е")
    value = _GENERIC_PUNCT.sub(" ", value)
    return _GENERIC_SPACES.sub(" ", value).strip()


def _generic_tokens(value: str) -> set[str]:
    cleaned = _generic_clean(value)
    return {
        token
        for token in cleaned.split()
        if len(token) >= 2 and token not in _GENERIC_STOPWORDS
    }


def _generic_numbers(value: str) -> set[str]:
    return {
        _GENERIC_SPACES.sub("", match.group(0).replace(",", ".").lower())
        for match in _GENERIC_NUMBER_RE.finditer(_generic_clean(value))
    }


def _delivery_days(offer: ProductOffer) -> int:
    if offer.delivery is None or offer.delivery.eta_max_hours is None:
        return 3
    return max(1, (offer.delivery.eta_max_hours + 23) // 24)


def _enrich_offer(offer: ProductOffer) -> ProductOffer:
    extracted = extract_offer_attributes(offer)
    attrs = merge_attributes(offer.attributes, extracted)
    canonical = canonicalize_characteristics(offer.characteristics, category=attrs.category)
    return offer.model_copy(update={"attributes": attrs, "canonical_characteristics": canonical})


def _enrich_result(result: ScrapeResult) -> ScrapeResult:
    return ScrapeResult(
        source=result.source,
        offers=[_enrich_offer(o) for o in result.offers],
        error=result.error,
        cached=result.cached,
    )
