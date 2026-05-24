"""LLM-based characteristic inference and offer explanation using Gemma.

When to use vs deterministic:
- Characteristic inference: ONLY when extract_query_attributes confidence < 0.4
  (regex extractor is confused, typically unusual categories: одежда, шины, оргтехника).
- Offer explanation: for top-5 offers that have mismatch_signals, to generate
  one natural-language sentence explaining why the offer doesn't match.
  Skipped for perfect matches (no mismatches needed).
"""

from __future__ import annotations

import json

import structlog
from ollama import AsyncClient

from pricepulse.config import get_settings
from pricepulse.domain.models import ProductAttributes

log = structlog.get_logger(__name__)

_INFER_SYSTEM = """Ты — эксперт по товарным характеристикам. Определи, что именно
ищет пользователь. Возвращай только те поля, которые явно указаны в запросе.
Ответ строго JSON (пропускай неизвестные поля):
{
  "category": "ноутбук/смартфон/принтер/одежда/шины/...",
  "brand": "бренд",
  "model": "модель",
  "color": "цвет",
  "storage_gb": число,
  "ram_gb": число,
  "size": "размер",
  "material": "материал",
  "season": "лето/зима/весна-осень/всесезон",
  "gender": "мужской/женский/унисекс",
  "tyre_width_mm": число,
  "tyre_profile": число,
  "tyre_rim_inch": число,
  "print_technology": "лазерный/струйный",
  "color_print": true/false,
  "wifi": true/false
}"""

_EXPLAIN_SYSTEM = """Ты — ассистент товарного портала. Кратко (1 предложение, макс. 120 символов)
объясни на русском, почему товар подходит или не подходит под запрос.
Используй конкретные характеристики из списка несовпадений.
Ответ строго JSON: {"explanation": "текст"}"""

_INT_FIELDS = {
    "storage_gb", "ram_gb", "tyre_width_mm", "tyre_profile", "tyre_rim_inch",
    "print_speed_ppm", "screen_size_inch", "refresh_rate_hz", "brightness_lm",
    "capacity_l", "power_w", "megapixels", "density_gm2", "sheets_count",
    "pack_count", "sheet_capacity", "page_yield", "height_cm",
}
_BOOL_FIELDS = {"color_print", "wifi", "duplex", "studs", "original_consumable"}
_KNOWN_FIELDS = set(ProductAttributes.model_fields) - {"confidence", "raw", "extra"}


def _make_client() -> AsyncClient:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.ollama_api_key}"} if s.ollama_api_key else {}
    return AsyncClient(host=s.ollama_url, headers=headers)


def _coerce(key: str, value: object) -> object:
    if key in _INT_FIELDS:
        try:
            return int(float(str(value)))
        except (ValueError, TypeError):
            return None
    if key in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes", "да")
    return str(value).strip() if value is not None else None


async def llm_infer_characteristics(query: str) -> dict:
    """Ask Gemma to infer expected product characteristics from query.

    Returns dict with known ProductAttributes fields, coerced to correct types.
    Called only when deterministic extractor confidence < 0.4.
    """
    s = get_settings()
    try:
        resp = await _make_client().chat(
            model=s.ollama_text_model,
            messages=[
                {"role": "system", "content": _INFER_SYSTEM},
                {"role": "user", "content": f"Запрос: {query}"},
            ],
            format="json",
            options={"temperature": 0.1, "think": False},
        )
        raw = json.loads(resp["message"]["content"])
        result = {}
        for k, v in raw.items():
            if k not in _KNOWN_FIELDS or v in (None, "", []):
                continue
            coerced = _coerce(k, v)
            if coerced is not None:
                result[k] = coerced
        log.debug("llm_chars.inferred", query=query, result=result)
        return result
    except Exception as exc:
        log.debug("llm_chars.infer_error", error=str(exc))
    return {}


def apply_llm_characteristics(attrs: ProductAttributes, llm_data: dict) -> ProductAttributes:
    """Merge LLM-inferred fields into existing ProductAttributes.

    Only fills fields that are currently None (never overwrites existing values).
    Bumps confidence by 0.2 if anything was added.
    """
    updates = {}
    for key, value in llm_data.items():
        if key not in _KNOWN_FIELDS:
            continue
        if getattr(attrs, key, None) is not None:
            continue
        updates[key] = value
    if not updates:
        return attrs
    updates["confidence"] = min(1.0, attrs.confidence + 0.2)
    return attrs.model_copy(update=updates)


async def llm_explain_offer(
    query: str,
    offer_name: str,
    characteristics: dict[str, str],
    mismatches: list[str],
    unknowns: list[str],
) -> str | None:
    """Generate a short natural-language explanation for why an offer matches/doesn't.

    Returns explanation string or None on failure / when no mismatches exist.
    Called only for top-5 offers that have mismatch_signals.
    """
    if not mismatches:
        return None

    s = get_settings()
    chars_str = "; ".join(f"{k}: {v}" for k, v in list(characteristics.items())[:8]) or "нет данных"
    user_msg = (
        f"Запрос: {query}\n"
        f"Товар: {offer_name}\n"
        f"Характеристики: {chars_str}\n"
        f"Несовпадения: {', '.join(mismatches)}\n"
        f"Неизвестно: {', '.join(unknowns) if unknowns else 'нет'}"
    )
    try:
        resp = await _make_client().chat(
            model=s.ollama_text_model,
            messages=[
                {"role": "system", "content": _EXPLAIN_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            format="json",
            options={"temperature": 0.2, "think": False},
        )
        data = json.loads(resp["message"]["content"])
        explanation = (data.get("explanation") or "").strip()
        return explanation or None
    except Exception as exc:
        log.debug("llm_chars.explain_error", offer=offer_name, error=str(exc))
    return None
