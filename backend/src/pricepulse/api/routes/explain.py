"""SSE-streaming endpoint that asks a local Gemma model to explain why
a particular offer is a good deal — concrete numbers, no fluff.

POST /api/v1/explain  →  text/event-stream of `data: <fragment>` chunks
                          ending with `data: [DONE]`.

The frontend opens an EventSource on this and types the response into
a panel character-by-character. Backend just proxies Ollama's chat
stream; no business logic in the prompt path so it's cheap.
"""

from __future__ import annotations

import asyncio
import statistics
from collections.abc import AsyncIterator
from decimal import Decimal

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from ollama import AsyncClient
from pydantic import BaseModel, Field

from pricepulse.config import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/explain", tags=["search"])


SYSTEM_PROMPT = (
    "Ты — ИИ-консультант портала государственных закупок. Тебе дают одно "
    "предложение товара плюс контекст (другие предложения из той же выдачи) "
    "и задача — за 3-4 коротких предложения объяснить, почему именно ЭТО "
    "предложение выгодно ИЛИ невыгодно покупателю. ОБЯЗАТЕЛЬНО приводи "
    "конкретные цифры: процент от медианы, разница в рублях, рейтинг "
    "относительно среднего, кол-во отзывов. Пиши на русском, без воды, "
    "без эмодзи, не маркетинговый текст, тон — деловой аналитик. Не "
    "придумывай факты которых нет в данных."
)


class OfferDict(BaseModel):
    source: str
    name: str
    price: Decimal
    seller: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    url: str | None = None


class ExplainRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    offer: OfferDict
    # All offers from the search response (frontend already has them).
    # Used to compute median / min / avg / rating distribution without
    # re-running the search.
    all_offers: list[OfferDict] = Field(default_factory=list)


def _format_context(req: ExplainRequest) -> str:
    """Pure-Python pre-compute so the LLM doesn't have to do statistics."""
    prices = [float(o.price) for o in req.all_offers if o.price > 0]
    if not prices:
        return "Контекст: нет других предложений для сравнения."
    median = statistics.median(prices)
    avg = statistics.mean(prices)
    pmin, pmax = min(prices), max(prices)
    offer_price = float(req.offer.price)
    delta_med_pct = (offer_price - median) / median * 100 if median > 0 else 0
    ratings = [o.rating for o in req.all_offers if o.rating is not None]
    avg_rating = statistics.mean(ratings) if ratings else None
    n_sources = len({o.source for o in req.all_offers})

    lines = [
        f"Запрос: {req.query!r}",
        f"Это предложение: {req.offer.name} — {offer_price:.0f} ₽ "
        f"({req.offer.source.upper()}, продавец: {req.offer.seller or 'не указан'})",
    ]
    if req.offer.rating is not None:
        lines.append(
            f"Рейтинг этого предложения: {req.offer.rating:.1f}"
            + (f" / средний {avg_rating:.2f}" if avg_rating else "")
            + (f" по {req.offer.reviews_count} отзывам" if req.offer.reviews_count else "")
        )
    lines.extend([
        f"Всего предложений в выдаче: {len(prices)} из {n_sources} источников.",
        f"Цены: мин {pmin:.0f} ₽, медиана {median:.0f} ₽, "
        f"среднее {avg:.0f} ₽, макс {pmax:.0f} ₽.",
        f"Это предложение {'дешевле' if delta_med_pct < 0 else 'дороже'} "
        f"медианы на {abs(delta_med_pct):.1f}%.",
    ])
    return "\n".join(lines)


async def _gemma_stream(req: ExplainRequest) -> AsyncIterator[str]:
    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    client = AsyncClient(host=settings.ollama_url, headers=headers)
    model = settings.ollama_text_model

    user_prompt = _format_context(req) + (
        "\n\nЗа 3-4 предложения объясни выгодность ЭТОГО предложения. "
        "Используй конкретные цифры из контекста выше."
    )

    try:
        # Lazy fallback: if requested model isn't pulled, fall back to
        # whatever's available — query_clarification.py does the same.
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
                log.warning("explain.list_models_failed", error=str(exc))

        async for chunk in await client.chat(
            model=model,
            stream=True,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": 0.3},
        ):
            piece = (chunk.get("message") or {}).get("content") or ""
            if piece:
                yield piece
    except Exception as exc:
        log.warning("explain.gemma_failed", error=str(exc))
        # Don't blow up the stream — degrade to a static fallback so the
        # frontend sees *something* useful instead of "Ollama unreachable".
        yield (
            "AI-объяснение недоступно. Локальная модель не отвечает — "
            "проверьте Ollama. Ниже краткий анализ по цифрам:\n\n"
            + _format_context(req)
        )


@router.post("")
async def explain_offer(req: ExplainRequest) -> StreamingResponse:
    """SSE stream of Gemma chunks."""
    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for piece in _gemma_stream(req):
                # SSE protocol: every event is `data: <payload>\n\n`.
                # Replace newlines in the payload so they don't break the
                # frame — frontend rejoins via concatenation.
                safe = piece.replace("\n", "\\n")
                yield f"data: {safe}\n\n".encode()
                # Cooperative yield so the chunk flushes promptly.
                await asyncio.sleep(0)
        except Exception as exc:
            log.warning("explain.stream_crash", error=str(exc))
            yield f"data: [ERROR] {exc}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
