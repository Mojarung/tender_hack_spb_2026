"""Image-to-query extraction for product search.

The module intentionally returns a text query rather than offers. The existing
search pipeline then handles normalization, scraping, grouping and ranking.
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Any, Literal

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
        cache_key = f"image_query:v1:{self._settings.ollama_vision_model}:{digest}"
        if self._cache is not None:
            try:
                cached = await self._cache.get(cache_key)
            except Exception as exc:
                log.debug("image_query.cache_get_failed", error=str(exc))
            else:
                if cached:
                    return ImageQueryResult.model_validate(cached).model_copy(
                        update={"cached": True, "took_ms": int((time.perf_counter() - started) * 1000)}
                    )

        result = await self._call_ollama(image)
        result = result.model_copy(update={"took_ms": int((time.perf_counter() - started) * 1000), "cached": False})

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
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": _PROMPT,
                    "images": [base64.b64encode(image).decode("ascii")],
                },
            ],
            "stream": False,
            "format": _SCHEMA,
            "options": {"temperature": 0.2, "top_p": 0.95, "top_k": 64, "num_predict": 220},
            "keep_alive": "5m",
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._settings.ollama_url.rstrip("/"),
                headers=headers,
                timeout=90.0,
            ) as client:
                response = await client.post("/api/chat", json=body)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ImageQueryError(f"ollama request failed: {exc}") from exc

        payload = response.json()
        content = ((payload.get("message") or {}).get("content") or "").strip()
        if not content:
            raise ImageQueryError("ollama returned empty response")
        try:
            parsed = orjson.loads(_extract_json_object(content))
            return ImageQueryResult.model_validate(parsed)
        except (orjson.JSONDecodeError, ValidationError) as exc:
            raise ImageQueryError("ollama returned invalid image query JSON") from exc


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


__all__ = [
    "ALLOWED_IMAGE_TYPES",
    "ImageQueryError",
    "ImageQueryExtractor",
    "ImageQueryResult",
    "image_hash",
]
