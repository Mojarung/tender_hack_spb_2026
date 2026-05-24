"""WB feedbacks fetcher: v2-first endpoint, photo URL parsing, shard fallback."""

from __future__ import annotations

import httpx
import pytest
import respx

from pricepulse.scrapers.wb_basket import feedbacks_host
from pricepulse.scrapers.wb_feedbacks import fetch_wb_feedbacks


def _sample_payload() -> dict:
    return {
        "feedbackCount": 2,
        "feedbackCountWithPhoto": 1,
        "feedbackCountWithVideo": 0,
        "valuation": "3.5",
        "valuationDistribution": {"5": 1, "2": 1},
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
                "photos": [
                    {"id": 1, "key": "6/d7a25475-cd60-412a-985f-11007bf8d84f", "isReady": True},
                ],
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
        ],
    }


# Pick the canonical host the scraper would hit so we mock the
# right shard first (same CRC-16/ARC picker the code uses).
_IMT = 777
_PRIMARY = feedbacks_host(_IMT)
_FALLBACK = "feedbacks1.wb.ru" if _PRIMARY == "feedbacks2.wb.ru" else "feedbacks2.wb.ru"


@pytest.mark.asyncio
@respx.mock
async def test_fetches_v2_and_sorts_newest_first() -> None:
    respx.get(f"https://{_PRIMARY}/feedbacks/v2/{_IMT}").mock(
        return_value=httpx.Response(200, json=_sample_payload())
    )
    page = await fetch_wb_feedbacks(_IMT, limit=5)
    assert page.total == 2
    assert page.with_photo == 1
    assert page.valuation == 3.5
    assert len(page.feedbacks) == 2
    assert page.feedbacks[0].id == "fb-1"
    assert page.feedbacks[0].rating == 5
    assert page.feedbacks[0].joined_text == "Отличное качество быстрая доставка"
    assert page.feedbacks[1].cons == "плохо подошло"
    # photo URL built from key
    pu = page.feedbacks[0].photo_urls
    assert pu and pu[0]["mini"].startswith("https://feedback-06.wbbasket.ru/")


@pytest.mark.asyncio
@respx.mock
async def test_falls_back_to_other_shard_on_first_failure() -> None:
    respx.get(f"https://{_PRIMARY}/feedbacks/v2/{_IMT}").mock(
        side_effect=httpx.ConnectError("boom")
    )
    respx.get(f"https://{_FALLBACK}/feedbacks/v2/{_IMT}").mock(
        return_value=httpx.Response(200, json=_sample_payload())
    )
    page = await fetch_wb_feedbacks(_IMT, limit=5)
    assert len(page.feedbacks) == 2
    assert page.feedbacks[0].id == "fb-1"


@pytest.mark.asyncio
@respx.mock
async def test_falls_back_to_v1_when_v2_unavailable() -> None:
    # Both v2 shards 500 → tries v1; first shard returns OK.
    respx.get(f"https://{_PRIMARY}/feedbacks/v2/{_IMT}").mock(
        return_value=httpx.Response(500)
    )
    respx.get(f"https://{_FALLBACK}/feedbacks/v2/{_IMT}").mock(
        return_value=httpx.Response(500)
    )
    respx.get(f"https://{_PRIMARY}/feedbacks/v1/{_IMT}").mock(
        return_value=httpx.Response(200, json=_sample_payload())
    )
    page = await fetch_wb_feedbacks(_IMT, limit=5)
    assert len(page.feedbacks) == 2


@pytest.mark.asyncio
@respx.mock
async def test_returns_empty_when_all_shards_fail() -> None:
    for host in (_PRIMARY, _FALLBACK):
        for ver in ("v2", "v1"):
            respx.get(f"https://{host}/feedbacks/{ver}/{_IMT}").mock(
                return_value=httpx.Response(500)
            )
    page = await fetch_wb_feedbacks(_IMT, limit=5)
    assert page.feedbacks == []
    assert page.total == 0
