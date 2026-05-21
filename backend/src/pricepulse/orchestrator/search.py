import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer, SourceGroup
from pricepulse.enrichment.normalize import normalize_query
from pricepulse.scrapers.base import ScraperProtocol, ScrapeResult
from pricepulse.scrapers.ozon import OzonScraper
from pricepulse.scrapers.runet import RunetScraper
from pricepulse.scrapers.wb import WildberriesScraper
from pricepulse.scrapers.yandex_market import YandexMarketScraper


class SearchOrchestrator:
    """Fan-out a query to every active source, merge results, optionally stream."""

    def __init__(self) -> None:
        # Registry of available adapters. Adding a source = adding one entry here.
        self._registry: dict[SourceKind, ScraperProtocol] = {
            SourceKind.WB: WildberriesScraper(),
            SourceKind.OZON: OzonScraper(),
            SourceKind.YA_MARKET: YandexMarketScraper(),
            SourceKind.RUNET: RunetScraper(),
        }

    def _pick(self, sources: list[SourceKind] | None) -> list[ScraperProtocol]:
        if not sources:
            return list(self._registry.values())
        return [self._registry[s] for s in sources if s in self._registry]

    async def run(
        self,
        query: str,
        max_per_source: int,
        sources: list[SourceKind] | None = None,
    ) -> tuple[NormalizedQuery, list[SourceGroup]]:
        normalized = await normalize_query(query)
        adapters = self._pick(sources)

        async def _safe(adapter: ScraperProtocol) -> ScrapeResult:
            try:
                return await adapter.search(normalized, limit=max_per_source)
            except Exception as exc:  # noqa: BLE001 — adapters must not crash the request
                return ScrapeResult(source=adapter.source, offers=[], error=str(exc))

        results = await asyncio.gather(*[_safe(a) for a in adapters])
        return normalized, [_to_group(r) for r in results]

    async def stream(
        self,
        query: str,
        max_per_source: int,
        sources: list[SourceKind] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield SSE-shaped events as adapters report offers."""
        normalized = await normalize_query(query)
        yield "query_normalized", normalized.model_dump()

        adapters = self._pick(sources)
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        async def _run(adapter: ScraperProtocol) -> None:
            await queue.put(("source_started", {"source": adapter.source.value}))

            async def on_offer(offer: ProductOffer) -> None:
                await queue.put(("offer", {"source": adapter.source.value, "offer": offer.model_dump()}))

            try:
                result = await adapter.search(normalized, limit=max_per_source, on_offer=on_offer)
                await queue.put(
                    (
                        "source_finished",
                        {
                            "source": adapter.source.value,
                            "count": len(result.offers),
                            "min_price": str(min((o.price for o in result.offers), default=None))
                            if result.offers
                            else None,
                        },
                    )
                )
            except Exception as exc:  # noqa: BLE001
                await queue.put(
                    ("error", {"source": adapter.source.value, "message": str(exc)})
                )

        workers = [asyncio.create_task(_run(a)) for a in adapters]

        async def _drain() -> None:
            await asyncio.gather(*workers)
            await queue.put(("done", {}))

        drainer = asyncio.create_task(_drain())
        try:
            while True:
                event_type, payload = await queue.get()
                yield event_type, payload
                if event_type == "done":
                    break
        finally:
            drainer.cancel()


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
