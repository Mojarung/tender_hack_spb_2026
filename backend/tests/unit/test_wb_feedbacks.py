"""WB feedbacks fetcher: parsing, shard fallback."""

from __future__ import annotations

import httpx
import pytest
import respx

from pricepulse.scrapers.wb_feedbacks import fetch_wb_feedbacks


def _sample_payload() -> dict:
    return {
        "feedbacks": [
            {
                "id": "fb-1",
                "nmId": 100,
                "productValuation": 5,
                "text": "Отличное качество",
                "pros": "быстрая доставка",
                "cons": "",
                "color": "красный",
                "size": "M",
                "createdDate": "2026-05-20T10:00:00Z",
                "votes": {"pluses": 12, "minuses": 0},
            },
            {
                "id": "fb-2",
                "nmId": 100,
                "productValuation": 2,
                "text": "Размер не тот",
                "pros": "",
                "cons": "плохо подошло",
                "color": "синий",
                "size": "L",
                "createdDate": "2026-05-19T10:00:00Z",
                "votes": {"pluses": 1, "minuses": 3},
            },
        ]
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetches_and_sorts_newest_first() -> None:
    respx.get("https://feedbacks1.wb.ru/feedbacks/v1/777").mock(
        return_value=httpx.Response(200, json=_sample_payload())
    )
    out = await fetch_wb_feedbacks(777, limit=5)
    assert len(out) == 2
    # Newest-first
    assert out[0].id == "fb-1"
    assert out[0].rating == 5
    assert out[0].joined_text == "Отличное качество быстрая доставка"
    assert out[1].cons == "плохо подошло"


@pytest.mark.asyncio
@respx.mock
async def test_falls_back_to_second_shard_on_first_failure() -> None:
    respx.get("https://feedbacks1.wb.ru/feedbacks/v1/777").mock(
        side_effect=httpx.ConnectError("boom")
    )
    respx.get("https://feedbacks2.wb.ru/feedbacks/v1/777").mock(
        return_value=httpx.Response(200, json=_sample_payload())
    )
    out = await fetch_wb_feedbacks(777, limit=5)
    assert len(out) == 2
    assert out[0].id == "fb-1"


@pytest.mark.asyncio
@respx.mock
async def test_returns_empty_when_all_shards_fail() -> None:
    respx.get("https://feedbacks1.wb.ru/feedbacks/v1/777").mock(
        return_value=httpx.Response(500)
    )
    respx.get("https://feedbacks2.wb.ru/feedbacks/v1/777").mock(
        return_value=httpx.Response(500)
    )
    out = await fetch_wb_feedbacks(777, limit=5)
    assert out == []
