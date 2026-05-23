"""Image-to-query extraction for product search.

The module intentionally returns a text query rather than offers. The existing
search pipeline then handles normalization, scraping, grouping and ranking.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import orjson
import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator

from pricepulse.cache.redis_cache import RedisCache
from pricepulse.config import get_settings

log = structlog.get_logger(__name__)

AllowedImageType = Literal["image/jpeg", "image/png", "image/webp"]

ALLOWED_IMAGE_TYPES: set[str] = {"image/jpeg", "image/png", "image/webp"}

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "category": {"type": ["string", "null"]},
        "brand": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "color": {"type": ["string", "null"]},
        "attributes": {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean", "null"]}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "alternatives": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["query", "confidence", "alternatives"],
}

_SYSTEM = (
    "Ты извлекаешь из фотографии товара короткий поисковый запрос для российских маркетплейсов. "
    "Отвечай строго JSON по схеме. Не выдумывай бренд или модель, если они не видны. "
    "Запрос должен быть на русском, 2-8 слов, без цены, без слов вроде 'фото' или 'картинка'. "
    "Для одежды укажи тип, пол, цвет, бренд если виден. Для шин укажи размерность, сезон, шипы и бренд если видны. "
    "Для оргтехники укажи тип устройства, бренд, модель и технологию печати если видны."
)

_PROMPT = "Сформируй marketplace search query по изображению товара. Верни только JSON."

_JSON_INSTRUCTION = (
    'Ответ должен быть одним JSON-объектом без markdown: '
    '{"query":"черный лазерный принтер hp","confidence":0.8,"alternatives":["принтер hp"]}'
)


class ImageQueryResult(BaseModel):
    query: str = Field(..., min_length=1, max_length=160)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    category: str | None = None
    brand: str | None = None
    model: str | None = None
    color: str | None = None
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    alternatives: list[str] = Field(default_factory=list)
    took_ms: int = 0
    cached: bool = False

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = " ".join(value.strip().split())
        if not stripped:
            raise ValueError("empty query")
        return stripped

    @field_validator("alternatives")
    @classmethod
    def _strip_alternatives(cls, value: list[str]) -> list[str]:
        return [" ".join(v.strip().split()) for v in value if v.strip()][:5]


class ImageQueryError(RuntimeError):
    """Raised when image query extraction cannot produce a safe result."""


def image_hash(image: bytes) -> str:
    return hashlib.sha256(image).hexdigest()


class ImageQueryExtractor:
    def __init__(self, *, cache: RedisCache | None = None) -> None:
        self._settings = get_settings()
        self._cache = cache

    async def describe(self, image: bytes, content_type: str) -> ImageQueryResult:
        started = time.perf_counter()
        self._validate_image(image, content_type)

        digest = image_hash(image)
        log.info(
            "image_query.start",
            image_sha256=digest[:12],
            content_type=content_type,
            size_bytes=len(image),
            model=self._settings.ollama_vision_model,
            ollama_host=urlparse(self._settings.ollama_url).netloc,
        )
        cache_key = f"image_query:v1:{self._settings.ollama_vision_model}:{digest}"
        if self._cache is not None:
            try:
                cached = await self._cache.get(cache_key)
            except Exception as exc:
                log.debug("image_query.cache_get_failed", error=str(exc))
            else:
                if cached:
                    log.info("image_query.cache_hit", image_sha256=digest[:12])
                    return ImageQueryResult.model_validate(cached).model_copy(
                        update={"cached": True, "took_ms": int((time.perf_counter() - started) * 1000)}
                    )

        result = await self._call_ollama(image)
        result = result.model_copy(update={"took_ms": int((time.perf_counter() - started) * 1000), "cached": False})
        log.info(
            "image_query.success",
            image_sha256=digest[:12],
            query=result.query,
            confidence=result.confidence,
            took_ms=result.took_ms,
        )

        if self._cache is not None and result.confidence >= 0.4:
            try:
                await self._cache.set(
                    cache_key,
                    result.model_dump(mode="json", exclude={"took_ms", "cached"}),
                    ttl_seconds=self._settings.image_search_cache_ttl_seconds,
                )
            except Exception as exc:
                log.debug("image_query.cache_set_failed", error=str(exc))
        return result

    def _validate_image(self, image: bytes, content_type: str) -> None:
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise ImageQueryError("unsupported image type")
        if not image:
            raise ImageQueryError("empty image")
        if len(image) > self._settings.image_search_max_bytes:
            raise ImageQueryError("image too large")

    async def _call_ollama(self, image: bytes) -> ImageQueryResult:
        headers: dict[str, str] = {}
        if self._settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {self._settings.ollama_api_key}"
        body = {
            "model": self._settings.ollama_vision_model,
            "prompt": f"{_SYSTEM}\n\n{_PROMPT}\n{_JSON_INSTRUCTION}",
            "images": [base64.b64encode(image).decode("ascii")],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "top_p": 0.95, "top_k": 64, "num_predict": 220},
            "keep_alive": "5m",
        }
        try:
            response = await self._post_generate_with_retry(headers, body)
        except httpx.HTTPError as exc:
            raise ImageQueryError(f"ollama request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            log.warning(
                "image_query.ollama_invalid_http_json",
                status_code=response.status_code,
                body=response.text[:1000],
            )
            raise ImageQueryError("ollama returned invalid response JSON") from exc
        content = (payload.get("response") or (payload.get("message") or {}).get("content") or "").strip()
        if not content:
            log.warning(
                "image_query.ollama_empty_response",
                status_code=response.status_code,
                payload_keys=list(payload.keys()),
            )
            raise ImageQueryError("ollama returned empty response")
        try:
            parsed = orjson.loads(_extract_json_object(content))
            if isinstance(parsed, str):
                parsed = {"query": parsed, "confidence": 0.35, "alternatives": []}
            return ImageQueryResult.model_validate(parsed)
        except (orjson.JSONDecodeError, ValidationError) as exc:
            log.warning(
                "image_query.ollama_invalid_model_json",
                response_snippet=content[:1000],
                error=str(exc),
            )
            fallback = _fallback_text_query(content)
            if fallback is not None:
                log.info("image_query.fallback_plain_text", query=fallback.query)
                return fallback
            raise ImageQueryError("ollama returned invalid image query JSON") from exc

    async def _post_generate_with_retry(self, headers: dict[str, str], body: dict[str, Any]) -> httpx.Response:
        last_exc: httpx.HTTPError | None = None
        retry_statuses = {502, 503, 504}
        upstream = self._settings.ollama_url.rstrip("/")
        for attempt in range(3):
            try:
                log.info(
                    "image_query.ollama_request",
                    attempt=attempt + 1,
                    ollama_host=urlparse(upstream).netloc,
                    model=body.get("model"),
                )
                async with httpx.AsyncClient(
                    base_url=upstream,
                    headers={**headers, "Connection": "close"},
                    limits=httpx.Limits(max_keepalive_connections=0, max_connections=5),
                    timeout=90.0,
                ) as client:
                    response = await client.post("/api/generate", json=body)
                    if response.status_code in retry_statuses and attempt < 2:
                        log.warning(
                            "image_query.ollama_retryable_status",
                            attempt=attempt + 1,
                            status_code=response.status_code,
                            body=response.text[:1000],
                        )
                        await asyncio.sleep(0.4 * (attempt + 1))
                        continue
                    if response.is_error:
                        log.warning(
                            "image_query.ollama_http_error",
                            attempt=attempt + 1,
                            status_code=response.status_code,
                            body=response.text[:1000],
                        )
                    response.raise_for_status()
                    log.info(
                        "image_query.ollama_response",
                        attempt=attempt + 1,
                        status_code=response.status_code,
                        response_bytes=len(response.content),
                    )
                    return response
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exc = exc
                log.warning(
                    "image_query.ollama_transport_error",
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if attempt == 2:
                    break
                await asyncio.sleep(0.4 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        raise ImageQueryError("ollama request failed after retries")


def _extract_json_object(content: str) -> str:
    """Gemma can wrap JSON in markdown fences even with structured output."""
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return stripped
    return stripped[start : end + 1]


def _fallback_text_query(content: str) -> ImageQueryResult | None:
    """Keep image search usable when Gemma ignores JSON mode."""
    query = content.strip().strip('"\'`')
    if query.startswith(("{", "[")):
        return None
    prefixes = (
        "query:",
        "поисковый запрос:",
        "запрос:",
        "search query:",
    )
    lowered = query.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            query = query[len(prefix) :].strip().strip('"\'`')
            break
    query = " ".join(query.replace("\r", " ").replace("\n", " ").split())
    if not query or len(query) > 160:
        return None
    return ImageQueryResult(query=query, confidence=0.35, alternatives=[])


__all__ = [
    "ALLOWED_IMAGE_TYPES",
    "ImageQueryError",
    "ImageQueryExtractor",
    "ImageQueryResult",
    "image_hash",
]
