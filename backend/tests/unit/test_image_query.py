from __future__ import annotations

import base64

import httpx
import pytest
import respx
from httpx import AsyncClient

from pricepulse.config import get_settings
from pricepulse.enrichment.image_query import ImageQueryError, ImageQueryExtractor, image_hash

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _generate_url() -> str:
    return get_settings().ollama_url.rstrip("/") + "/api/generate"


@respx.mock
@pytest.mark.asyncio
async def test_image_query_extractor_calls_ollama_and_parses_json() -> None:
    route = respx.post(_generate_url()).mock(
        return_value=httpx.Response(
            200,
            json={
                "response": (
                    '{"query":"черные кроссовки adidas","confidence":0.82,'
                    '"brand":"adidas","color":"black","alternatives":["кроссовки adidas"]}'
                )
            },
        )
    )

    result = await ImageQueryExtractor(cache=None).describe(_PNG, "image/png")

    assert result.query == "черные кроссовки adidas"
    assert result.confidence == 0.82
    assert result.brand == "adidas"
    assert result.color == "black"
    assert result.cached is False
    assert route.called
    request = route.calls.last.request
    assert request is not None
    expected_key = get_settings().ollama_api_key
    if expected_key:
        assert request.headers.get("authorization") == f"Bearer {expected_key}"
    else:
        assert request.headers.get("authorization") is None
    assert image_hash(_PNG)


@pytest.mark.asyncio
async def test_image_query_extractor_rejects_unsupported_type() -> None:
    with pytest.raises(ImageQueryError, match="unsupported image type"):
        await ImageQueryExtractor(cache=None).describe(_PNG, "image/gif")


@respx.mock
@pytest.mark.asyncio
async def test_image_query_extractor_rejects_bad_model_json() -> None:
    respx.post(_generate_url()).mock(
        return_value=httpx.Response(200, json={"response": "{}"}),
    )

    with pytest.raises(ImageQueryError, match="invalid image query JSON"):
        await ImageQueryExtractor(cache=None).describe(_PNG, "image/png")


@respx.mock
@pytest.mark.asyncio
async def test_image_query_extractor_falls_back_to_plain_text_query() -> None:
    respx.post(_generate_url()).mock(
        return_value=httpx.Response(200, json={"response": "Поисковый запрос: черный офисный принтер"}),
    )

    result = await ImageQueryExtractor(cache=None).describe(_PNG, "image/png")

    assert result.query == "черный офисный принтер"
    assert result.confidence == 0.35


@respx.mock
@pytest.mark.asyncio
async def test_image_query_extractor_accepts_markdown_json() -> None:
    respx.post(_generate_url()).mock(
        return_value=httpx.Response(
            200,
            json={
                "response": '```json\n{"query":"офисный принтер","confidence":0.7,"alternatives":[]}\n```'
            },
        ),
    )

    result = await ImageQueryExtractor(cache=None).describe(_PNG, "image/png")

    assert result.query == "офисный принтер"
    assert result.confidence == 0.7


@respx.mock
@pytest.mark.asyncio
async def test_image_query_extractor_defaults_missing_confidence() -> None:
    respx.post(_generate_url()).mock(
        return_value=httpx.Response(200, json={"response": '{"query":"красные кроссовки"}'}),
    )

    result = await ImageQueryExtractor(cache=None).describe(_PNG, "image/png")

    assert result.query == "красные кроссовки"
    assert result.confidence == 0.5


@respx.mock
@pytest.mark.asyncio
async def test_image_describe_endpoint(client: AsyncClient) -> None:
    respx.post(_generate_url()).mock(
        return_value=httpx.Response(
            200,
            json={"response": '{"query":"лазерный принтер hp","confidence":0.76,"alternatives":[]}'},
        ),
    )

    response = await client.post(
        "/api/v1/search/image/describe",
        files={"image": ("printer.png", _PNG, "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "лазерный принтер hp"
    assert body["confidence"] == 0.76
    assert body["cached"] is False


@pytest.mark.asyncio
async def test_image_describe_endpoint_rejects_gif(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/search/image/describe",
        files={"image": ("animated.gif", _PNG, "image/gif")},
    )

    assert response.status_code == 415
