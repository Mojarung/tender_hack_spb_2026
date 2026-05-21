"""SearchOrchestrator: contract tests with stubbed adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.orchestrator.search import SearchOrchestrator
from pricepulse.scrapers.base import OnOffer, ScrapeResult


class _Stub:
    def __init__(self, source: SourceKind, *, offers: list[ProductOffer] | None = None,
                 raises: Exception | None = None):
        self.source = source
        self._offers = offers or []
        self._raises = raises

    async def search(
        self, query: NormalizedQuery, limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        if self._raises:
            raise self._raises
        offers = self._offers[:limit]
        if on_offer is not None:
            for o in offers:
                await on_offer(o)
        return ScrapeResult(source=self.source, offers=offers)


def _offer(source: SourceKind, name: str, price: int) -> ProductOffer:
    return ProductOffer(
        source=source,
        name=name,
        price=Decimal(price),
        currency="RUB",
        url=f"https://example.com/{source.value}/{name.replace(' ', '-')}",
        image=None,
        characteristics={},
        seller=None,
        rating=None,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


@pytest.mark.asyncio
async def test_run_merges_four_sources_and_isolates_crash() -> None:
    adapters = {
        SourceKind.WB: _Stub(SourceKind.WB, offers=[_offer(SourceKind.WB, "wb-1", 100)]),
        SourceKind.OZON: _Stub(SourceKind.OZON, raises=RuntimeError("ozon down")),
        SourceKind.YA_MARKET: _Stub(
            SourceKind.YA_MARKET,
            offers=[_offer(SourceKind.YA_MARKET, "ya-1", 200),
                    _offer(SourceKind.YA_MARKET, "ya-2", 300)],
        ),
        SourceKind.RUNET: _Stub(SourceKind.RUNET, offers=[_offer(SourceKind.RUNET, "ru-1", 250)]),
    }
    runet_fb = _Stub(SourceKind.RUNET, offers=[])
    orch = SearchOrchestrator(adapters=adapters, runet_fallback=runet_fb)

    normalized, groups, _ = await orch.run("iphone", max_per_source=10)

    assert normalized.normalized == "iphone"
    by_src = {g.source: g for g in groups}
    assert by_src[SourceKind.WB].count == 1
    assert by_src[SourceKind.WB].min_price == 100
    assert by_src[SourceKind.OZON].count == 0
    assert by_src[SourceKind.OZON].error and "ozon down" in by_src[SourceKind.OZON].error
    assert by_src[SourceKind.YA_MARKET].count == 2
    assert by_src[SourceKind.RUNET].count == 1


@pytest.mark.asyncio
async def test_stream_yields_done_event_last() -> None:
    adapters = {
        SourceKind.WB: _Stub(SourceKind.WB, offers=[_offer(SourceKind.WB, "wb-1", 100)]),
        SourceKind.OZON: _Stub(SourceKind.OZON, offers=[]),
        SourceKind.YA_MARKET: _Stub(SourceKind.YA_MARKET, offers=[]),
        SourceKind.RUNET: _Stub(SourceKind.RUNET, offers=[]),
    }
    orch = SearchOrchestrator(adapters=adapters,
                              runet_fallback=_Stub(SourceKind.RUNET, offers=[]))

    events = [e async for e in orch.stream("iphone", max_per_source=5)]
    types = [t for t, _ in events]

    assert types[0] == "query_normalized"
    assert types[-1] == "done"
    assert "offer" in types
    finished = [p for t, p in events if t == "source_finished"]
    assert len(finished) == 4


@pytest.mark.asyncio
async def test_top_deals_are_ranked_descending_by_score() -> None:
    adapters = {
        SourceKind.WB: _Stub(SourceKind.WB, offers=[
            _offer(SourceKind.WB, "premium", 100000),
            _offer(SourceKind.WB, "budget", 9000),
        ]),
        SourceKind.OZON: _Stub(SourceKind.OZON, offers=[
            _offer(SourceKind.OZON, "mid", 50000),
        ]),
        SourceKind.YA_MARKET: _Stub(SourceKind.YA_MARKET, offers=[]),
        SourceKind.RUNET: _Stub(SourceKind.RUNET, offers=[]),
    }
    orch = SearchOrchestrator(adapters=adapters,
                              runet_fallback=_Stub(SourceKind.RUNET, offers=[]))
    _, _, top = await orch.run("anything", max_per_source=10)
    assert len(top) == 3
    # Strictly decreasing scores; rank matches order.
    assert [r.rank for r in top] == [1, 2, 3]
    assert top[0].score >= top[1].score >= top[2].score
    # The cheapest item should rank first thanks to negative price-z weight.
    assert top[0].offer.name == "budget"


@pytest.mark.asyncio
async def test_run_falls_back_to_megamarket_when_runet_empty() -> None:
    """If RunetScraper returns nothing without an error, we transparently
    try the Megamarket fallback adapter (see anti-bot.md §5.4)."""
    primary_runet = _Stub(SourceKind.RUNET, offers=[])
    fallback = _Stub(SourceKind.RUNET, offers=[_offer(SourceKind.RUNET, "mm-1", 999)])
    adapters = {
        SourceKind.WB: _Stub(SourceKind.WB, offers=[]),
        SourceKind.OZON: _Stub(SourceKind.OZON, offers=[]),
        SourceKind.YA_MARKET: _Stub(SourceKind.YA_MARKET, offers=[]),
        SourceKind.RUNET: primary_runet,
    }
    orch = SearchOrchestrator(adapters=adapters, runet_fallback=fallback)

    _, groups, _ = await orch.run("anything", max_per_source=5)
    runet = next(g for g in groups if g.source == SourceKind.RUNET)
    assert runet.count == 1
    assert runet.offers[0].name == "mm-1"
