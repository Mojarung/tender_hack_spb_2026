"""Wildberries scraper: unit test with respx-mocked search.wb.ru."""

from __future__ import annotations

import httpx
import pytest
import respx

from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers.wb import _SEARCH_URL, WildberriesScraper


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
                    "feedbacks": 1234,
                    "nmReviewRating": 4.9,
                    "pics": 12,
                    "sizes": [{"price": {"basic": 8990000, "product": 6990000, "total": 6990000}}],
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


@pytest.mark.asyncio
@respx.mock
async def test_wb_handles_429_without_retry_and_opens_cooldown() -> None:
    route = respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(429, text="rate limited")
    )
    scraper = WildberriesScraper()
    result = await scraper.search(
        NormalizedQuery(raw="x", normalized="x"), limit=5,
    )
    assert route.call_count == 1
    assert result.offers == []
    assert result.error is not None

    result = await scraper.search(
        NormalizedQuery(raw="x", normalized="x"), limit=5,
    )
    assert route.call_count == 1
    assert result.offers == []
    assert result.error is not None


@pytest.mark.asyncio
@respx.mock
async def test_wb_returns_error_on_persistent_failure() -> None:
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))
    result = await WildberriesScraper().search(
        NormalizedQuery(raw="x", normalized="x"), limit=5,
    )
    assert result.offers == []
    assert result.error is not None
