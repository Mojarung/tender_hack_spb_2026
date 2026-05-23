"""Vision-LLM captcha solver: Gemma 4 via Ollama API.

Models:
    gemma4:e2b   — 2.3B, ~1.5 GB Q4, 60+ tok/s CPU   — live demo box
    gemma4:e4b   — 4.5B, ~5 GB   Q4, 30 tok/s CPU    — default (recommended)
    gemma4:26b   — MoE 3.8B/26B, ~14-18 GB Q4, RTX-3090/4090

Capabilities (MMMU Pro = 76.9% on Gemma 4 31B):
    • OCR distorted text (Yandex SmartCaptcha "text recognition")  WR ~85%
    • "click N silhouettes in order"                                WR ~75%
    • Identify object in grid                                       WR ~80%
    • Kaleidoscope puzzle                                           WR <30% — skip
"""

from __future__ import annotations

import base64
from typing import Literal

import httpx
import orjson
from pydantic import BaseModel, Field

CaptchaKind = Literal["text", "silhouettes", "click_object", "kaleidoscope"]

_PROMPTS: dict[CaptchaKind, str] = {
    "text": (
        "Это капча: на картинке искажённый текст (буквы, цифры). "
        "Распознай его и верни строго JSON: "
        '{"text": "<распознанная строка>"}. Никаких пояснений.'
    ),
    "silhouettes": (
        "Капча: сверху N силуэтов в ряд, снизу сетка пронумерованных "
        "(слева-направо, сверху-вниз: 0..8) квадратов. Верни JSON со списком "
        "номеров квадратов в порядке силуэтов слева-направо. "
        'Формат: {"clicks": [<int>, ...]}. Только JSON.'
    ),
    "click_object": (
        "Капча: сетка квадратов с изображениями. В тексте задачи указано, "
        "на какие объекты кликнуть. Верни JSON-список индексов квадратов "
        "(0..N-1, слева-направо, сверху-вниз). "
        'Формат: {"clicks": [<int>, ...]}.'
    ),
}


class _GenerateRequest(BaseModel):
    model: str
    prompt: str
    images: list[str]
    format: str = "json"
    stream: bool = False
    options: dict[str, float] = Field(default_factory=lambda: {"temperature": 0.0})


class VLMResult(BaseModel):
    """Raw model output already parsed as JSON."""

    text: str | None = None
    clicks: list[int] | None = None
    raw: dict


class VLMSolver:
    """Async client for Ollama's `/api/generate`."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        from pricepulse.config import get_settings

        settings = get_settings()
        headers = {}
        if settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url or settings.ollama_url,
            headers=headers,
            timeout=timeout_s,
        )
        self._model = model or settings.ollama_vision_model

    async def aclose(self) -> None:
        await self._client.aclose()

    async def solve(self, image: bytes, kind: CaptchaKind) -> VLMResult:
        if kind == "kaleidoscope":
            raise NotImplementedError("Kaleidoscope is not VLM-solvable; route to 2captcha")
        prompt = _PROMPTS[kind]
        body = _GenerateRequest(
            model=self._model,
            prompt=prompt,
            images=[base64.b64encode(image).decode("ascii")],
        ).model_dump()
        r = await self._client.post("/api/generate", json=body)
        r.raise_for_status()
        payload = r.json()
        parsed = orjson.loads(payload["response"])
        return VLMResult(text=parsed.get("text"), clicks=parsed.get("clicks"), raw=parsed)


__all__ = ["CaptchaKind", "VLMResult", "VLMSolver"]
