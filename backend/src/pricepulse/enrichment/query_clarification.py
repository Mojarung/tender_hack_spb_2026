"""Gemma-powered query disambiguation.

When a buyer types a query that bundles two unrelated things — "лодка
и яблоко", "iphone tiggo pro max", "холодильник + утюг" — searching
all marketplaces for the literal string returns useless mush. We
intercept early, ask the local LLM if the query is mixing categories,
and surface three options to the UI:

  1. <first category>          ("лодка")
  2. <second category>         ("яблоко")
  3. <the literal text>        ("лодка и яблоко" — escape hatch)

If the LLM fails or returns nothing useful, a small heuristic catches
the obvious "<noun> <conjunction> <noun>" pattern so we still surface a
clarification card instead of running a doomed search.
"""

import json
import re

import structlog
from ollama import AsyncClient

from pricepulse.config import get_settings
from pricepulse.domain.models import ClarificationOption, QueryClarification

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Ты — интеллектуальный ассистент портала закупок PricePulse.
Тебе дают сырой поисковый запрос. Реши, является ли он двусмысленным —
то есть содержит ли он сразу несколько РАЗНЫХ категорий товаров,
которые в нормальной жизни никогда не ищут вместе одним запросом.

ОЧЕНЬ ВАЖНО: запрос почти всегда двусмысленный, если в нём встречаются
два или более существительных, соединённых союзом «и», знаком «+»,
запятой или просто пробелом, и при этом эти существительные относятся
к РАЗНЫМ категориям товаров. Не упускай такие случаи.

Двусмысленные запросы (is_ambiguous=true):
  • "лодка и яблоко"          → лодка (водный транспорт) + яблоко (продукт)
  • "айфон + наушники"        → смартфон + аудио
  • "телевизор, мука"         → бытовая техника + продукты
  • "iphone pro tiggo pro"    → телефон Apple + автомобиль Chery
  • "кофеварка и шины"        → техника + автотовары
  • "молоток и собачий корм"  → инструменты + товары для животных
  • "ноутбук тостер"          → 2 не связанных товара через пробел

Однозначные запросы (is_ambiguous=false):
  • "iphone 15 pro max 256gb"
  • "кофемашина DeLonghi Magnifica"
  • "молоко 3.2% Простоквашино"
  • "шины michelin 205 55 r16"      (одна категория, спецификация)
  • "ноутбук asus 15 i5 16gb"       (одна категория, характеристики)

Если запрос двусмысленный:
  1) is_ambiguous = true
  2) reason = одна короткая фраза по-русски, например: "Запрос содержит
     разные категории товаров — что именно вы ищете?"
  3) options = ровно три варианта в таком порядке:
        a) Первый товар (короткое название без эмодзи + поле query =
           очищенный запрос только по этому товару, например "лодка")
        b) Второй товар (то же самое для второго)
        c) Поиск как есть (label="Искать как написано",
           text="Искать «<исходный запрос>» по всему каталогу",
           query=<исходный сырой запрос>)
     ВАЖНО: ровно три варианта, последний — обязательно "Искать как написано".

Если запрос однозначный:
  is_ambiguous = false, reason = null, options = []

Каждая опция содержит три строковых поля:
  - label: 2-4 слова без эмодзи и кавычек (например "Лодки и катера")
  - text:  короткая поясняющая строка для кнопки
           (например "Искать «лодка» — водный транспорт")
  - query: очищенный поисковый запрос для этого варианта

Ответ — строго JSON-объект:
{
  "is_ambiguous": boolean,
  "reason": string | null,
  "options": [{"label": string, "text": string, "query": string}, ...]
}
"""


# Conjunctions and separators that, when they sit between two clearly
# different noun-shaped tokens, almost always mean the user typed two
# distinct things into one box.
_SPLIT_RE = re.compile(
    r"\s+(?:и|или|плюс|and|or|with|с)\s+|\s*[,+&;|/]\s*|\s+",
    re.IGNORECASE,
)

# Tokens that don't on their own indicate a separate product — model
# numbers, sizes, units. Used to suppress the heuristic on queries like
# "ноутбук asus 15 i5 16gb".
_SPEC_TOKEN_RE = re.compile(
    r"^("
    r"\d+(?:[.,]\d+)?(?:[a-zа-я%/-]*)?"      # 256gb, 3.2%, 205/55, r16
    r"|[a-zа-я]\d+[a-zа-я0-9]*"               # i5, r16, m1, e4b
    r"|\d+[a-zа-я]+"                          # 256gb, 16gb
    r"|pro|max|plus|mini|ultra|lite|super"
    r"|pcs|шт|kg|кг|г|ml|мл|см|мм|м"
    r")$",
    re.IGNORECASE,
)

# Brand-like tokens that often follow a category noun. Don't fire the
# heuristic on these — "телевизор samsung" is clearly one product.
_BRAND_HINTS = {
    "samsung", "lg", "sony", "xiaomi", "apple", "asus", "lenovo", "hp",
    "dell", "huawei", "honor", "philips", "bosch", "siemens", "delonghi",
    "тефаль", "redmond", "polaris", "panasonic", "canon",
    "michelin", "nokian", "yokohama", "bridgestone", "continental",
    "epson", "brother", "cisco",
}


def _looks_clearly_ambiguous(query: str) -> tuple[str, str] | None:
    """Return (left, right) if the query splits into two distinct-noun
    halves on a conjunction/comma/plus. Used as a fallback so obvious
    cases like «лодка и яблоко» still trigger a clarification even
    when the LLM was sloppy."""
    raw = query.strip().lower()
    if not raw or len(raw) < 5:
        return None

    # Strong split signal: explicit Russian/English conjunction or
    # punctuation between non-empty halves.
    strong = re.split(r"\s+(?:и|или|плюс|and|or|with)\s+|\s*[,+&;]\s*", raw)
    halves = [h.strip() for h in strong if h.strip()]
    if len(halves) >= 2:
        left, right = halves[0], halves[1]
        if _is_noun_like(left) and _is_noun_like(right) \
                and not _shares_brand_or_spec(left, right):
            return left, right
    return None


def _is_noun_like(text: str) -> bool:
    """Crude: at least one Cyrillic/Latin word that isn't a spec token."""
    words = [w for w in re.split(r"\s+", text) if w]
    if not words:
        return False
    return any(not _SPEC_TOKEN_RE.match(w) for w in words)


def _shares_brand_or_spec(left: str, right: str) -> bool:
    """If one half is a brand name or pure spec block (e.g. "samsung",
    "256gb pro"), don't treat it as a separate noun — the user is
    just specifying one product."""
    lw, rw = left.split(), right.split()
    if len(lw) <= 1 and lw[0] in _BRAND_HINTS:
        return True
    if len(rw) <= 1 and rw[0] in _BRAND_HINTS:
        return True
    if all(_SPEC_TOKEN_RE.match(w) for w in rw):
        return True
    if all(_SPEC_TOKEN_RE.match(w) for w in lw):
        return True
    return False


def _heuristic_clarification(query: str) -> QueryClarification | None:
    """Build a 3-option clarification card from the conjunction split.
    Returns None when the query doesn't look split-able."""
    split = _looks_clearly_ambiguous(query)
    if not split:
        return None
    left, right = split
    return QueryClarification(
        is_ambiguous=True,
        reason="Запрос содержит несколько разных товаров — что именно вы ищете?",
        options=[
            ClarificationOption(
                label=left.capitalize(),
                text=f"Искать только «{left}»",
                query=left,
            ),
            ClarificationOption(
                label=right.capitalize(),
                text=f"Искать только «{right}»",
                query=right,
            ),
            ClarificationOption(
                label="Искать как написано",
                text=f"Искать «{query}» по всему каталогу",
                query=query,
            ),
        ],
    )


def _ensure_three_options(
    base_query: str, parsed: QueryClarification,
) -> QueryClarification:
    """Post-process the LLM's answer so we always end with the
    expected three-option layout (incl. the literal-search escape
    hatch as the last option) when the model said it's ambiguous."""
    if not parsed.is_ambiguous:
        return parsed
    options = list(parsed.options or [])
    # Guarantee the last option is "search as typed".
    has_raw = any(
        (o.query or "").strip().lower() == base_query.strip().lower()
        for o in options
    )
    if not has_raw:
        options.append(ClarificationOption(
            label="Искать как написано",
            text=f"Искать «{base_query}» по всему каталогу",
            query=base_query,
        ))
    # Trim or pad to 3.
    if len(options) > 3:
        options = [*options[:2], options[-1]]
    elif len(options) < 2:
        # Model misfired — fall back to heuristic for the alternatives.
        heur = _heuristic_clarification(base_query)
        if heur is not None:
            return heur
        return QueryClarification(is_ambiguous=False, reason=None, options=[])
    return QueryClarification(
        is_ambiguous=True, reason=parsed.reason, options=options,
    )


async def check_and_clarify_query(user_query: str) -> QueryClarification:
    """Analyze the query with the local LLM, with a heuristic safety net."""
    settings = get_settings()
    heuristic = _heuristic_clarification(user_query)

    headers = {}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    client = AsyncClient(host=settings.ollama_url, headers=headers)
    model_name = settings.ollama_text_model

    try:
        # Resolve the model only when talking to local Ollama (no api key).
        if not settings.ollama_api_key:
            try:
                models_response = await client.list()
                available_models = [m.model for m in models_response.models]
                log.debug("query_clarification.ollama_models", available=available_models)
                if model_name not in available_models:
                    if settings.ollama_vision_model in available_models:
                        model_name = settings.ollama_vision_model
                    elif available_models:
                        model_name = available_models[0]
            except Exception as list_err:
                log.warning("query_clarification.list_models_failed", error=str(list_err))

        response = await client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Проанализируй запрос: '{user_query}'"},
            ],
            format="json",
            options={"temperature": 0.1, "think": False},
        )

        content = response["message"]["content"]
        parsed_response = json.loads(content)

        options = []
        for opt in parsed_response.get("options", []):
            options.append(ClarificationOption(
                label=opt.get("label", ""),
                text=opt.get("text", ""),
                query=opt.get("query", ""),
            ))
        parsed = QueryClarification(
            is_ambiguous=bool(parsed_response.get("is_ambiguous", False)),
            reason=parsed_response.get("reason"),
            options=options,
        )
        result = _ensure_three_options(user_query, parsed)
        # If LLM said "fine" but heuristic disagrees on an obvious
        # conjunction query — trust the heuristic.
        if not result.is_ambiguous and heuristic is not None:
            log.info("query_clarification.heuristic_override", query=user_query)
            return heuristic
        return result

    except Exception as e:
        log.warning("query_clarification.failed", query=user_query, error=str(e))
        # Even when the LLM is down, surface the heuristic clarification
        # for obvious split queries so the user isn't stranded.
        if heuristic is not None:
            return heuristic
        return QueryClarification(is_ambiguous=False, reason=None, options=[])
