"""Wildberries scraper: unit test with mocked browser + CDN.

The old test mocked `search.wb.ru/v18` via respx, but that endpoint is
now behind WB's Page Guard (PG-41/PG-42 PoW). The new path goes through
a persistent nodriver tab + DOM-scrape + parallel basket-CDN enrichment.
We mock the three injection points:

    WBBrowserSearch.dom_search   → stubs from a fake DOM source
    wb_card.fetch_card           → fake card.json with chars + price
    wb_feedbacks.fetch_wb_feedbacks → fake reviews page

The orchestrator-level smoke test (`tests/test_health.py::test_search_
empty_groups`) covers the full path end-to-end against the real
orchestrator.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers import wb as wb_module
from pricepulse.scrapers.wb import WildberriesScraper
from pricepulse.scrapers.wb_card import WbCardDetail
from pricepulse.scrapers.wb_feedbacks import WbFeedback, WbFeedbacksPage


def _fake_dom_result() -> dict[str, Any]:
    return {
        "source": "dom",
        "products": [
            {
                "nm": 254634586,
                "url": "https://www.wildberries.ru/catalog/254634586/detail.aspx",
                "name": "Смартфон iPhone 15 128 ГБ",
                "price_rub": 69900,
                "brand": "Apple",
                "image": "https://basket-05.wbbasket.ru/vol2546/part254634/254634586/images/big/1.webp",
            },
            {
                "nm": 100000000,
                "url": "https://www.wildberries.ru/catalog/100000000/detail.aspx",
                "name": "Тест-товар без цены",
                "price_rub": None,
                "brand": "Generic",
                "image": "",
            },
        ],
    }


def _fake_card(nm_id: int, price_rub: int | None = 69900) -> WbCardDetail:
    return WbCardDetail(
        nm_id=nm_id,
        shard="05",
        imt_id=195729411,
        imt_name="Смартфон iPhone 15 128 ГБ",
        description="Описание тестовое",
        brand="Apple",
        category_root="Электроника",
        category="Смартфоны",
        photo_count=2,
        has_video=False,
        characteristics=[("Основные", "Бренд", "Apple")],
        gallery=[
            "https://basket-05.wbbasket.ru/vol2546/part254634/254634586/images/big/1.webp",
            "https://basket-05.wbbasket.ru/vol2546/part254634/254634586/images/big/2.webp",
        ],
        price_rub=price_rub,
    )


def _fake_feedbacks_page() -> WbFeedbacksPage:
    return WbFeedbacksPage(
        feedbacks=[
            WbFeedback(
                id="abc", nm_id=254634586, rating=5,
                text="Отличный товар, всё пришло в срок и качественно сделано.",
                pros="", cons="", color="black", size="",
                created="2026-05-15T10:00:00Z", pluses=3, minuses=0,
                photo_urls=[], video_urls=None,
            ),
        ],
        total=1234, with_photo=200, with_video=12,
        valuation=4.9, valuation_distribution={"5": 900, "4": 200},
    )


class _FakeBrowser:
    """Mocks WBBrowserSearch enough to satisfy `await get_wb_browser()` +
    `.dom_search(query)`."""

    def __init__(self, dom_result: dict[str, Any] | None = None) -> None:
        self._dom = dom_result if dom_result is not None else _fake_dom_result()

    async def dom_search(self, query: str, **_: Any) -> dict[str, Any]:
        return self._dom


@pytest.mark.asyncio
async def test_wb_full_pipeline_with_enrichment() -> None:
    fake_browser = _FakeBrowser()

    with patch.object(wb_module, "get_wb_browser", AsyncMock(return_value=fake_browser)), \
         patch.object(wb_module, "fetch_card",
                      AsyncMock(side_effect=lambda nm, **kw: _fake_card(nm))) as card_mock, \
         patch.object(wb_module, "fetch_wb_feedbacks",
                      AsyncMock(return_value=_fake_feedbacks_page())) as fb_mock:

        result = await WildberriesScraper(reviews_per_offer=5).search(
            NormalizedQuery(raw="iphone 15", normalized="iphone 15"),
            limit=10,
        )

    assert result.error is None
    # Second stub (no price) gets backfilled from card.json — so both
    # validate. card+feedbacks are called per stub.
    assert card_mock.await_count == 2
    assert fb_mock.await_count == 2
    assert len(result.offers) == 2

    offer = result.offers[0]
    assert offer.source.value == "wb"
    assert offer.name.startswith("Смартфон iPhone 15")
    assert offer.price == 69900
    assert offer.rating == 4.9        # backfilled from feedbacks valuation
    assert offer.reviews_count == 1234
    assert offer.images and len(offer.images) >= 1
    assert offer.reviews and offer.reviews[0]["score"] == 5


@pytest.mark.asyncio
async def test_wb_returns_error_on_dom_search_failure() -> None:
    fake_browser = _FakeBrowser({"error": "captcha appeared"})
    with patch.object(wb_module, "get_wb_browser", AsyncMock(return_value=fake_browser)):
        result = await WildberriesScraper().search(
            NormalizedQuery(raw="x", normalized="x"), limit=5,
        )
    assert result.offers == []
    assert result.error and "captcha" in result.error


@pytest.mark.asyncio
async def test_wb_returns_empty_on_no_stubs() -> None:
    fake_browser = _FakeBrowser({"source": "dom", "products": []})
    with patch.object(wb_module, "get_wb_browser", AsyncMock(return_value=fake_browser)):
        result = await WildberriesScraper().search(
            NormalizedQuery(raw="x", normalized="x"), limit=5,
        )
    assert result.offers == []
    assert result.error is None
