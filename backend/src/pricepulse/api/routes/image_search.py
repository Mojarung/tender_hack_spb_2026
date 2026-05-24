"""POST /api/v1/search/image — turn an uploaded photo into a search query.

The user drops a photo of a product into the search bar (think: «нашёл
у себя на столе непонятную штуку — что это и сколько стоит»). We pass
the bytes to Gemma's vision model (gemma4:e4b) with a tight Russian
prompt that asks for a 3-7 word search query suitable for a Russian
marketplace catalogue. The returned string is then handed straight to
the normal /search flow.

Why a separate endpoint and not bake it into /search:
  * Lets the frontend show the recognised query before searching, so
    the user can edit it ("нет, это не сахар, это соль") before we
    burn a full multi-source scrape.
  * Keeps the prompt and image-upload contract isolated from the rest
    of the search code.

External-API rule (final_presa.pdf p.5): we call Ollama, not Google /
OpenAI / etc. Ollama can be either local (no api key) or self-hosted
in the cloud profile — both routed through the same settings.
"""

from __future__ import annotations

import base64

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile
from ollama import AsyncClient
from pydantic import BaseModel

from pricepulse.config import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/search", tags=["search"])

_MAX_BYTES = 8 * 1024 * 1024     # 8 MB — bigger photos get auto-rejected
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}

# Carefully tuned: forces a TERSE Russian search query, blocks emojis /
# markdown / extra commentary. The model loves to add "Конечно, это —"
# without the explicit DON'T list.
SYSTEM_PROMPT = (
    "Ты помогаешь искать товар по фотографии для интернет-магазина. "
    "Тебе дают одно изображение. Сформулируй короткий поисковый "
    "запрос на русском языке, такой же как пользователь вбил бы в "
    "поиск Wildberries или Ozon.\n\n"
    "Жёсткие правила:\n"
    "  • Только 3-7 слов на русском (можно латиница в названиях брендов).\n"
    "  • Опиши САМ товар: что это, бренд если виден, ключевые признаки "
    "(цвет, размер, материал) если они помогут отличить.\n"
    "  • Не описывай фон, поверхность, людей, упаковку — только товар.\n"
    "  • Никаких объяснений «это похоже на…», «возможно…», «вижу…».\n"
    "  • Никаких эмодзи, кавычек, markdown.\n"
    "  • Ответ — ОДНА строка с запросом, и ничего больше.\n\n"
    "Примеры правильных ответов:\n"
    "  кофемашина DeLonghi черная\n"
    "  ноутбук ASUS 15 серый\n"
    "  кроссовки Nike белые мужские\n"
    "  чайник электрический Tefal стеклянный\n"
    "  iPhone 15 Pro синий\n"
)

USER_INSTRUCTION = (
    "Сформулируй поисковый запрос для этого товара."
)


class ImageQueryResponse(BaseModel):
    query: str
    used_model: str


@router.post("/image", response_model=ImageQueryResponse)
async def search_image(image: UploadFile = File(...)) -> ImageQueryResponse:
    if image.content_type not in _ALLOWED_MIME:
        raise HTTPException(415, f"Unsupported image type: {image.content_type or '?'}")

    raw = await image.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, f"Image too large ({len(raw)} bytes, max {_MAX_BYTES})")

    b64 = base64.b64encode(raw).decode("ascii")

    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    client = AsyncClient(host=settings.ollama_url, headers=headers)
    model = settings.ollama_vision_model

    try:
        response = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_INSTRUCTION, "images": [b64]},
            ],
            options={"temperature": 0.2, "think": False},
        )
    except Exception as exc:
        log.warning("image_search.gemma_failed", error=str(exc))
        raise HTTPException(502, f"Vision model error: {exc}") from exc

    content = (response.get("message") or {}).get("content") or ""
    query = _clean(content)
    if not query:
        raise HTTPException(422, "Model produced an empty query")
    return ImageQueryResponse(query=query, used_model=model)


def _clean(text: str) -> str:
    """Strip the model's occasional preamble / quotes / trailing
    punctuation so the result is a clean search query."""
    line = text.strip().splitlines()[0] if text.strip() else ""
    # Drop common preambles even though the prompt forbids them — models drift.
    for prefix in ("это ", "вижу ", "на фото ", "на изображении ",
                   "Это ", "Вижу ", "На фото ", "На изображении ",
                   "Запрос: ", "Поисковый запрос: ", "запрос: "):
        if line.startswith(prefix):
            line = line[len(prefix):]
    line = line.strip("\"'«»‹›„‟ .,!?:;—–")
    # Hard length cap so a runaway answer doesn't blow up the URL.
    if len(line) > 120:
        line = line[:120].rsplit(" ", 1)[0]
    return line


__all__ = ["router"]
