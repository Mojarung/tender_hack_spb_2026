from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import (
    DeliveryInfo,
    NormalizedQuery,
    ProductAttributes,
    ProductOffer,
    RankedOffer,
    SourceGroup,
)


def _offer() -> ProductOffer:
    return ProductOffer(
        source=SourceKind.WB,
        name="Apple iPhone 15 128GB Black",
        price=Decimal("53196"),
        currency="RUB",
        url="https://www.wildberries.ru/catalog/444655005/detail.aspx",
        image=None,
        characteristics={"feedbacks": "4"},
        attributes=ProductAttributes(
            category="smartphone",
            brand="apple",
            model="iphone 15",
            color="black",
            storage_gb=128,
            confidence=0.9,
        ),
        delivery=DeliveryInfo(
            city="Москва",
            region_id="1259570991",
            region_source="wb_geo",
            warehouse_id="507",
            distance_marketplace=77,
            eta_min_hours=2,
            eta_max_hours=40,
            stock=44,
            confidence=0.9,
        ),
        seller="Wildberries",
        rating=5.0,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


@pytest.mark.asyncio
async def test_search_api_returns_attributes_and_delivery(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class StubOrchestrator:
        async def run(
            self,
            **kwargs: Any,
        ) -> tuple[NormalizedQuery, list[SourceGroup], list[RankedOffer]]:
            calls.append(kwargs)
            offer = _offer()
            query = NormalizedQuery(
                raw=kwargs["query"],
                normalized="iphone 15 black",
                attributes=ProductAttributes(
                    category="smartphone",
                    brand="apple",
                    model="iphone 15",
                    color="black",
                    confidence=0.8,
                ),
            )
            group = SourceGroup(
                source=SourceKind.WB,
                count=1,
                min_price=offer.price,
                median_price=offer.price,
                offers=[offer],
            )
            return query, [group], [RankedOffer(offer=offer, score=1.23, rank=1)]

    monkeypatch.setattr("pricepulse.api.routes.search.SearchOrchestrator", StubOrchestrator)

    response = await client.post(
        "/api/v1/search",
        json={"query": "айфон 15 черный", "max_per_source": 3, "city": "Москва"},
    )

    assert response.status_code == 200
    body = response.json()
    assert calls == [{
        "query": "айфон 15 черный",
        "max_per_source": 3,
        "sources": None,
        "nofix": False,
        "city": "Москва",
    }]
    assert body["query"]["attributes"]["model"] == "iphone 15"
    assert body["groups"][0]["median_price"] == "53196"
    offer = body["groups"][0]["offers"][0]
    assert offer["attributes"]["color"] == "black"
    assert offer["attributes"]["storage_gb"] == 128
    assert offer["delivery"]["city"] == "Москва"
    assert offer["delivery"]["eta_max_hours"] == 40
    assert body["top_deals"][0]["offer"]["delivery"]["warehouse_id"] == "507"


@pytest.mark.asyncio
async def test_search_api_validates_city_length(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/search",
        json={"query": "iphone", "max_per_source": 3, "city": "x" * 121},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_stream_passes_city_and_streams_attributes(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class StubStreamOrchestrator:
        async def stream(self, **kwargs: Any):
            calls.append(kwargs)
            yield "query_normalized", {
                "raw": kwargs["query"],
                "normalized": "iphone 15 black",
                "expansions": [],
                "attributes": {"category": "smartphone", "model": "iphone 15"},
            }
            yield "done", {"took_ms": 1}

    monkeypatch.setattr("pricepulse.api.routes.stream.SearchOrchestrator", StubStreamOrchestrator)

    response = await client.get(
        "/api/v1/search/stream",
        params={"query": "iphone 15 black", "max_per_source": 2, "city": "Москва"},
    )

    assert response.status_code == 200
    assert calls == [{"query": "iphone 15 black", "max_per_source": 2, "city": "Москва"}]
    text = response.text
    assert "event: query_normalized" in text
    assert '"model": "iphone 15"' in text
    assert "event: done" in text
