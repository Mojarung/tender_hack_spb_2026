"""Wildberries scraper: unit test with respx-mocked search.wb.ru."""

from __future__ import annotations

import httpx
import pytest
import respx

from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers.wb import _GEO_URL, _SEARCH_URL, WildberriesScraper


def _mock_response_body() -> dict:
    return {
        "data": {
            "products": [
                {
                    "id": 254634586,
                    "root": 195729411,
                    "brand": "Apple",
                    "name": "Смартфон iPhone 15 128 ГБ",
                    "supplier": "PSC",
                    "supplierRating": 4.8,
                    "colors": [{"name": "черный", "id": 0}],
                    "subjectId": 515,
                    "subjectParentId": 6258,
                    "weight": 0.335,
                    "volume": 5,
                    "totalQuantity": 12,
                    "feedbacks": 1234,
                    "nmReviewRating": 4.9,
                    "pics": 12,
                    "sizes": [{
                        "price": {"basic": 8990000, "product": 6990000, "total": 6990000},
                        "wh": 507,
                        "time1": 2,
                        "time2": 24,
                        "stocks": [{"wh": 507, "dist": 77, "qty": 12, "time1": 2, "time2": 24}],
                    }],
                },
                {
                    "id": 100000000,
                    "brand": "Generic",
                    "name": "Тест-товар без цены",
                    "sizes": [{"price": {}}],
                },
            ]
        }
    }


@pytest.mark.asyncio
@respx.mock
async def test_wb_parses_two_products_skips_priceless() -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_mock_response_body())
    )
    result = await WildberriesScraper().search(
        NormalizedQuery(raw="iphone 15", normalized="iphone 15"),
        limit=10,
    )
    assert result.error is None
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.source.value == "wb"
    assert offer.price == 69900
    assert "iPhone 15" in offer.name
    assert str(offer.url).startswith("https://www.wildberries.ru/catalog/")
    assert offer.image and "wbbasket.ru" in str(offer.image)
    assert offer.attributes is not None
    assert offer.attributes.category == "smartphone"
    assert offer.attributes.brand == "apple"
    assert offer.attributes.color == "black"
    assert offer.attributes.storage_gb == 128
    assert offer.delivery is not None
    assert offer.delivery.warehouse_id == "507"
    assert offer.delivery.eta_max_hours == 24
    assert offer.delivery.stock == 12


@pytest.mark.asyncio
@respx.mock
async def test_wb_resolves_city_to_delivery_region() -> None:
    geo = respx.get(_GEO_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "address": "Москва",
                "xinfo": "appType=1&curr=rub&dest=1259570991&spp=30",
            },
        )
    )
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=_mock_response_body()))

    result = await WildberriesScraper(city="Москва").search(
        NormalizedQuery(raw="iphone 15", normalized="iphone 15"),
        limit=10,
    )

    assert geo.called
    assert result.offers[0].delivery is not None
    assert result.offers[0].delivery.city == "Москва"
    assert result.offers[0].delivery.region_id == "1259570991"
    assert result.offers[0].delivery.region_source == "wb_geo"


@pytest.mark.asyncio
@respx.mock
async def test_wb_unknown_city_falls_back_to_default_dest_without_geo_call() -> None:
    geo = respx.get(_GEO_URL).mock(return_value=httpx.Response(500))
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=_mock_response_body()))

    result = await WildberriesScraper(city="Екатеринбург").search(
        NormalizedQuery(raw="iphone 15", normalized="iphone 15"),
        limit=10,
    )

    assert not geo.called
    assert result.offers[0].delivery is not None
    assert result.offers[0].delivery.city == "Екатеринбург"
    assert result.offers[0].delivery.region_source == "default_unknown_city"


@pytest.mark.asyncio
@respx.mock
async def test_wb_geo_failure_falls_back_to_default_dest() -> None:
    respx.get(_GEO_URL).mock(return_value=httpx.Response(500, text="boom"))
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=_mock_response_body()))

    result = await WildberriesScraper(city="Москва").search(
        NormalizedQuery(raw="iphone 15", normalized="iphone 15"),
        limit=10,
    )

    assert result.offers[0].delivery is not None
    assert result.offers[0].delivery.city == "Москва"
    assert result.offers[0].delivery.region_source == "default_geo_failed"


@pytest.mark.asyncio
@respx.mock
async def test_wb_handles_429_and_retries() -> None:
    route = respx.get(_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(200, json=_mock_response_body()),
        ]
    )
    result = await WildberriesScraper().search(
        NormalizedQuery(raw="x", normalized="x"), limit=5,
    )
    assert route.call_count == 2
    assert len(result.offers) == 1


@pytest.mark.asyncio
@respx.mock
async def test_wb_returns_error_on_persistent_failure() -> None:
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))
    result = await WildberriesScraper().search(
        NormalizedQuery(raw="x", normalized="x"), limit=5,
    )
    assert result.offers == []
    assert result.error is not None
