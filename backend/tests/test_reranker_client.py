"""Unit tests for RerankerClient.

Uses respx to mock httpx without touching the network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import ProductOffer
from pricepulse.enrichment.reranker_client import RerankerClient, _offer_to_doc


def _make_offer(name: str, price: str = "1000", source: SourceKind = SourceKind.OZON) -> ProductOffer:
    return ProductOffer(
        source=source,
        name=name,
        price=Decimal(price),
        url=f"https://ozon.ru/product/{name.replace(' ', '-')}",  # type: ignore[arg-type]
        fetched_at=datetime(2026, 5, 24, tzinfo=UTC),
    )


# ── _offer_to_doc ────────────────────────────────────────────────────────────

def test_offer_to_doc_name_only() -> None:
    offer = _make_offer("Кроссовки Nike Air Max")
    doc = _offer_to_doc(offer)
    assert doc == "Кроссовки Nike Air Max"


def test_offer_to_doc_with_chars() -> None:
    offer = _make_offer("Кроссовки Nike").model_copy(update={
        "characteristics": {"бренд": "Nike", "цвет": "красный", "гарантия": "12 мес"},
    })
    doc = _offer_to_doc(offer)
    assert "бренд: Nike" in doc
    assert "цвет: красный" in doc
    assert "гарантия" not in doc  # blacklisted


def test_offer_to_doc_caps_at_8_chars() -> None:
    chars = {f"key{i}": f"val{i}" for i in range(20)}
    offer = _make_offer("Товар").model_copy(update={"characteristics": chars})
    doc = _offer_to_doc(offer)
    # At most 8 characteristic parts + 1 name
    assert doc.count(":") <= 8


# ── RerankerClient.enabled ───────────────────────────────────────────────────

def test_disabled_when_url_empty() -> None:
    client = RerankerClient(url="")
    assert not client.enabled


def test_enabled_when_url_set() -> None:
    client = RerankerClient(url="http://localhost:8081")
    assert client.enabled


# ── RerankerClient.rerank — disabled passthrough ─────────────────────────────

@pytest.mark.anyio
async def test_rerank_disabled_returns_unchanged() -> None:
    client = RerankerClient(url="")
    offers = [_make_offer("A"), _make_offer("B")]
    result = await client.rerank("query", offers)
    assert result is offers  # exact same object, no copy


@pytest.mark.anyio
async def test_rerank_empty_offers_returns_empty() -> None:
    client = RerankerClient(url="http://localhost:8081")
    result = await client.rerank("query", [])
    assert result == []


# ── RerankerClient.rerank — happy path ───────────────────────────────────────

_BASE = "http://localhost:8081"


@pytest.mark.anyio
@respx.mock
async def test_rerank_happy_path() -> None:
    offers = [
        _make_offer("Худи Adidas"),        # index 0 — irrelevant
        _make_offer("Nike Air Max 90"),    # index 1 — best match
        _make_offer("Nike Air Max 270"),   # index 2 — second match
    ]
    api_results = [
        {"index": 1, "score": 0.993, "document": "Nike Air Max 90"},
        {"index": 2, "score": 0.915, "document": "Nike Air Max 270"},
        {"index": 0, "score": 0.0002, "document": "Худи Adidas"},
    ]
    respx.post(f"{_BASE}/rerank").mock(return_value=httpx.Response(200, json={"results": api_results}))

    client = RerankerClient(url=_BASE)
    result = await client.rerank("nike air max", offers)

    assert len(result) == 3
    assert result[0].name == "Nike Air Max 90"
    assert result[1].name == "Nike Air Max 270"
    assert result[2].name == "Худи Adidas"
    assert abs(result[0].rerank_score - 0.993) < 1e-6
    assert abs(result[2].rerank_score - 0.0002) < 1e-6


# ── RerankerClient.rerank — graceful degradation ─────────────────────────────

@pytest.mark.anyio
@respx.mock
async def test_rerank_timeout_returns_original() -> None:
    offers = [_make_offer("A"), _make_offer("B")]
    respx.post(f"{_BASE}/rerank").mock(side_effect=httpx.TimeoutException("timed out"))

    client = RerankerClient(url=_BASE)
    result = await client.rerank("query", offers)

    assert result == offers
    assert all(o.rerank_score is None for o in result)


@pytest.mark.anyio
@respx.mock
async def test_rerank_server_error_returns_original() -> None:
    offers = [_make_offer("A"), _make_offer("B")]
    respx.post(f"{_BASE}/rerank").mock(return_value=httpx.Response(500, text="Internal Server Error"))

    client = RerankerClient(url=_BASE)
    result = await client.rerank("query", offers)

    assert result == offers


@pytest.mark.anyio
@respx.mock
async def test_rerank_bad_json_returns_original() -> None:
    offers = [_make_offer("A")]
    respx.post(f"{_BASE}/rerank").mock(return_value=httpx.Response(200, text="not-json{{{"))

    client = RerankerClient(url=_BASE)
    result = await client.rerank("query", offers)

    assert result == offers
