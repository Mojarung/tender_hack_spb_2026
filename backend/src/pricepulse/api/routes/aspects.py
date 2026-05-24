"""POST /api/v1/aspects — local-LLM aspect extraction from product reviews.

Takes a batch of review texts (up to ~30), asks Gemma to surface the
top recurring pros / cons with rough mention counts, and returns a
flat JSON the frontend turns into ✅/❌ chips.

We deliberately bypass rubert-tiny2 (smaller / faster but only emits a
single sentiment label) — the demo wants aspect-level granularity
("✅ Качество сборки 87 % / ❌ Доставка 34 %") which only an LLM
produces cheaply enough.

Cached in Redis for 24 h per (offer_url, review_count) so repeated
opens of the same modal are instant.
"""

from __future__ import annotations

import hashlib
import json

import structlog
from fastapi import APIRouter, HTTPException
from ollama import AsyncClient
from pydantic import BaseModel, Field

from pricepulse.api.cache import get_search_cache
from pricepulse.config import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/aspects", tags=["search"])

_TTL = 24 * 3600
_MAX_REVIEWS = 30


SYSTEM_PROMPT = (
    "Ты — аналитик пользовательских отзывов. На вход — массив отзывов о "
    "товаре на русском языке. На выход — СТРОГО JSON с тремя ключами: "
    '"pros" (массив объектов {"label": "<2-3 слова>", "mentions": <int>}), '
    '"cons" (то же), "score" (целое число от 0 до 100 — итоговый sentiment-индекс). '
    "В pros — самые часто упоминаемые ДОСТОИНСТВА (3-5 шт.), в cons — "
    "НЕДОСТАТКИ (3-5 шт.). Метки короткие: «качество сборки», «дешёвый "
    "пластик», «быстрая доставка», «маркий экран». Если отзывов мало или "
    "одни плюсы — cons пустой. Никаких объяснений, никаких эмодзи в "
    "ответе, никакого markdown, только чистый JSON-объект."
)


class AspectsRequest(BaseModel):
    offer_url: str = Field(..., min_length=8, max_length=2048)
    reviews: list[str] = Field(default_factory=list, max_length=200)


class Aspect(BaseModel):
    label: str
    mentions: int


class AspectsResponse(BaseModel):
    pros: list[Aspect] = Field(default_factory=list)
    cons: list[Aspect] = Field(default_factory=list)
    score: int = 50
    n_reviews_used: int = 0


def _cache_key(req: AspectsRequest) -> str:
    h = hashlib.sha1(
        f"{req.offer_url}|{len(req.reviews)}".encode(),
        usedforsecurity=False,
    ).hexdigest()
    return f"aspects:{h}"


def _normalise(raw: dict) -> AspectsResponse:
    def _aspects(key: str) -> list[Aspect]:
        out: list[Aspect] = []
        for item in (raw.get(key) or [])[:6]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()[:40]
            try:
                mentions = int(item.get("mentions") or 0)
            except (TypeError, ValueError):
                mentions = 0
            if label:
                out.append(Aspect(label=label, mentions=mentions))
        return out
    pros = _aspects("pros")
    cons = _aspects("cons")
    try:
        score = max(0, min(100, int(raw.get("score") or 50)))
    except (TypeError, ValueError):
        score = 50
    return AspectsResponse(pros=pros, cons=cons, score=score)


@router.post("", response_model=AspectsResponse)
async def aspects(req: AspectsRequest) -> AspectsResponse:
    reviews = [r.strip() for r in req.reviews if isinstance(r, str) and r.strip()]
    if not reviews:
        raise HTTPException(422, "no review texts provided")
    reviews = reviews[:_MAX_REVIEWS]

    cache = await get_search_cache()
    key = _cache_key(req)
    if cache is not None:
        try:
            cached = await cache.get(key)
        except Exception as exc:
            log.debug("aspects.cache_get_failed", error=str(exc))
            cached = None
        if cached:
            cached.setdefault("n_reviews_used", len(reviews))
            return AspectsResponse(**cached)

    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    client = AsyncClient(host=settings.ollama_url, headers=headers)
    model = settings.ollama_text_model

    try:
        if not settings.ollama_api_key:
            try:
                models_response = await client.list()
                available = [m.model for m in models_response.models]
                if model not in available:
                    if settings.ollama_vision_model in available:
                        model = settings.ollama_vision_model
                    elif available:
                        model = available[0]
            except Exception as exc:
                log.warning("aspects.list_models_failed", error=str(exc))

        joined = "\n".join(f"— {r[:400]}" for r in reviews)
        response = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Отзывы ({len(reviews)} шт.):\n{joined}"},
            ],
            format="json",
            options={"temperature": 0.2, "think": False},
        )
        content = (response.get("message") or {}).get("content") or "{}"
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            log.warning("aspects.bad_json", error=str(exc), content=content[:200])
            return AspectsResponse(score=50, n_reviews_used=len(reviews))
        result = _normalise(parsed)
        result = AspectsResponse(
            pros=result.pros, cons=result.cons, score=result.score,
            n_reviews_used=len(reviews),
        )
    except Exception as exc:
        log.warning("aspects.gemma_failed", error=str(exc))
        return AspectsResponse(score=50, n_reviews_used=len(reviews))

    if cache is not None:
        try:
            await cache.set(key, result.model_dump(), ttl_seconds=_TTL)
        except Exception as exc:
            log.debug("aspects.cache_set_failed", error=str(exc))
    return result


__all__ = ["router"]
