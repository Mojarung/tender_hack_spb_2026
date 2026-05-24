"""LLM-based query normalization using Gemma via Ollama Cloud.

When to use vs deterministic:
- Spell correction: ONLY when SAGE microservice is down/disabled AND query has
  Cyrillic chars AND >= 2 words. Single-word brand typos are handled faster by
  RapidFuzz thesaurus.
- Synonyms: ONLY when curated thesaurus returns nothing AND query is short
  (1-3 content words). Specific model numbers (iphone 15 pro max 256gb) have
  no useful synonyms.
"""

from __future__ import annotations

import json
import re

import structlog
from ollama import AsyncClient

from pricepulse.config import get_settings

log = structlog.get_logger(__name__)

_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
_MODEL_NUMBER = re.compile(r"\b[a-zA-Z]{1,8}\s*\d{1,5}\b")

_OPTIMIZE_SYSTEM = """Ты — оптимизатор поисковых запросов для маркетплейсов (Wildberries, Ozon, Яндекс Маркет).

Задача: переписать запрос в лучшую форму для поиска товара.

Что менять:
• Транслитерацию с латиницы на русский: televisor→телевизор, noutbuk→ноутбук, telefon→телефон
• Разговорные слова → правильные товарные термины: телефон→смартфон, наушки→наушники, тв→телевизор
• Бренды на кириллице → латиница: самсунг→samsung, сони→sony, найк→nike
• Явные опечатки (только если уверен): ноубтук→ноутбук

Что НЕ менять:
• Конкретные модели: iphone 15, galaxy s24, rtx 4090
• Числа и характеристики: 256gb, 4к, 15 дюймов
• Бренды на латинице: samsung, apple, nike
• Специфичные запросы где смысл точный: «смартфон samsung», «кроссовки nike»
• Если запрос уже оптимален — верни без изменений

Ответ ТОЛЬКО JSON: {"optimized": "итоговый запрос", "changed": true/false, "note": "кратко что изменено, например 'телефон→смартфон' или null"}"""

_SYNONYM_SYSTEM = """Ты — эксперт товарного поиска для российских маркетплейсов.
Предложи до 3 альтернативных формулировок (синонимов), под которыми этот товар
реально продаётся. Только реальные синонимы, без выдумок.
Ответ строго JSON: {"synonyms": ["вариант1", "вариант2"]}"""


def _make_client() -> AsyncClient:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.ollama_api_key}"} if s.ollama_api_key else {}
    return AsyncClient(host=s.ollama_url, headers=headers)


def _is_category_query(query: str) -> bool:
    """Short Russian category query without a model number — synonyms may help."""
    words = [w for w in query.lower().split() if len(w) >= 3]
    if len(words) > 3:
        return False
    if _MODEL_NUMBER.search(query):
        return False
    return bool(_CYRILLIC.search(query))


async def llm_fix_query(query: str) -> tuple[str, list[str]]:
    """Gemma marketplace query optimizer.

    Handles: latin→russian transliteration (televisor→телевизор),
    colloquial→product terms (телефон→смартфон), cyrillic brands→latin (самсунг→samsung).
    Returns (optimized_query, [note]) or (query, []) if nothing changed / on error.
    """
    s = get_settings()
    try:
        resp = await _make_client().chat(
            model=s.ollama_text_model,
            messages=[
                {"role": "system", "content": _OPTIMIZE_SYSTEM},
                {"role": "user", "content": query},
            ],
            format="json",
            options={"temperature": 0.0, "think": False},
        )
        data = json.loads(resp["message"]["content"])
        optimized = (data.get("optimized") or "").strip()
        note = (data.get("note") or "").strip() or None
        if optimized and bool(data.get("changed")) and optimized != query:
            log.debug("llm_query.optimized", original=query, optimized=optimized)
            label = note or f"{query} → {optimized}"
            return optimized, [f"оптимизация: {label}"]
    except Exception as exc:
        log.debug("llm_query.optimize_error", error=str(exc))
    return query, []


async def llm_synonyms(query: str) -> list[str]:
    """Gemma synonym expansion for short category queries.

    Returns list of alternate query strings (empty on failure).
    """
    if not _is_category_query(query):
        return []

    s = get_settings()
    try:
        resp = await _make_client().chat(
            model=s.ollama_text_model,
            messages=[
                {"role": "system", "content": _SYNONYM_SYSTEM},
                {"role": "user", "content": query},
            ],
            format="json",
            options={"temperature": 0.3, "think": False},
        )
        data = json.loads(resp["message"]["content"])
        result = [s.strip() for s in (data.get("synonyms") or []) if s.strip() and s.strip() != query]
        log.debug("llm_query.synonyms", query=query, result=result)
        return result[:3]
    except Exception as exc:
        log.debug("llm_query.synonyms_error", error=str(exc))
    return []
