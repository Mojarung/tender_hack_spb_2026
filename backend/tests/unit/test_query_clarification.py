from unittest.mock import AsyncMock, patch

import pytest

from pricepulse.domain.models import QueryClarification
from pricepulse.enrichment.query_clarification import check_and_clarify_query


@pytest.mark.asyncio
@patch("pricepulse.enrichment.query_clarification.AsyncClient")
async def test_query_clarification_ambiguous_success(mock_client_class) -> None:
    # Setup mock responses
    mock_client = AsyncMock()
    mock_client_class.return_value = mock_client

    # Mock list models to return qwen3.5:9b
    mock_models_res = AsyncMock()
    mock_model_obj = AsyncMock()
    mock_model_obj.model = "qwen3.5:9b"
    mock_models_res.models = [mock_model_obj]
    mock_client.list.return_value = mock_models_res

    # Mock chat response
    mock_chat_res = {
        "message": {
            "content": (
                '{"is_ambiguous": true, "reason": "Запрос содержит разные категории товаров", '
                '"options": [{"label": "Смартфоны", "text": "Искать iPhone", "query": "iphone"}, '
                '{"label": "Авто", "text": "Искать Tiggo", "query": "tiggo"}]}'
            )
        }
    }
    mock_client.chat.return_value = mock_chat_res

    res = await check_and_clarify_query("iphone tiggo")

    assert isinstance(res, QueryClarification)
    assert res.is_ambiguous is True
    assert res.reason == "Запрос содержит разные категории товаров"
    assert len(res.options) == 2
    assert res.options[0].label == "Смартфоны"
    assert res.options[0].query == "iphone"
    assert res.options[1].label == "Авто"
    assert res.options[1].query == "tiggo"

@pytest.mark.asyncio
@patch("pricepulse.enrichment.query_clarification.AsyncClient")
async def test_query_clarification_unambiguous_success(mock_client_class) -> None:
    mock_client = AsyncMock()
    mock_client_class.return_value = mock_client

    mock_models_res = AsyncMock()
    mock_models_res.models = []
    mock_client.list.return_value = mock_models_res

    # Mock chat response
    mock_chat_res = {
        "message": {
            "content": '{"is_ambiguous": false, "reason": null, "options": []}'
        }
    }
    mock_client.chat.return_value = mock_chat_res

    res = await check_and_clarify_query("iphone 15 pro max")

    assert isinstance(res, QueryClarification)
    assert res.is_ambiguous is False
    assert res.reason is None
    assert len(res.options) == 0

@pytest.mark.asyncio
@patch("pricepulse.enrichment.query_clarification.AsyncClient")
async def test_query_clarification_fallback_on_error(mock_client_class) -> None:
    mock_client = AsyncMock()
    mock_client_class.return_value = mock_client

    # Mock chat to raise an exception
    mock_client.list.side_effect = Exception("Ollama connection refused")
    mock_client.chat.side_effect = Exception("Ollama error")

    res = await check_and_clarify_query("any query")

    assert isinstance(res, QueryClarification)
    assert res.is_ambiguous is False
    assert res.reason is None
    assert len(res.options) == 0
