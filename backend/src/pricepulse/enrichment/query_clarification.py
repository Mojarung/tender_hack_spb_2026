import json

import structlog
from ollama import AsyncClient

from pricepulse.config import get_settings
from pricepulse.domain.models import ClarificationOption, QueryClarification

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Ты — интеллектуальный ассистент портала поставщиков PricePulse.
Твоя задача — проанализировать поисковый запрос пользователя и определить, является ли он двусмысленным
(содержит ли он одновременно несколько разных категорий товаров или несвязанных сущностей,
которые обычно ищутся отдельно).

Примеры двусмысленных (неоднозначных) запросов:
1. "iphone pro tiggo pro max" -> телефон Apple iPhone и автомобиль/автотовары Chery Tiggo.
2. "кофеварка и шины" -> бытовая техника и автотовары.

Примеры однозначных запросов:
1. "iphone 15 pro max 256gb"
2. "кофемашина DeLonghi"
3. "молоко 3.2%"

Если запрос двусмысленный:
1. Установи "is_ambiguous": true.
2. Напиши краткое вежливое пояснение в "reason" на русском языке
   (например, "Запрос содержит разные категории товаров. Что именно вы ищете?").
3. Предложи 2-3 варианта уточнения в списке "options". Каждый вариант должен содержать:
   - "label": Короткое название БЕЗ эмодзи (например: "Смартфоны Apple iPhone",
     "Автомобили Chery Tiggo", "Искать как написано").
   - "text": Поясняющий текст для кнопки (например: "Искать \"iphone 15 pro max\" (в категории Электроника)",
     "Искать \"chery tiggo pro max\" (в категории Автотовары)",
     "Искать \"iphone pro tiggo pro max\" по всему каталогу").
   - "query": Очищенный точный поисковый запрос для этого варианта (например: "iphone 15 pro max",
     "chery tiggo pro max", "iphone pro tiggo pro max").

Важно: Последний вариант в "options" всегда должен быть поиском по исходному сырому запросу
"Искать как написано" с исходным текстом запроса в поле "query"!

Если запрос однозначный:
1. Установи "is_ambiguous": false.
2. Установи "reason": null.
3. Установи "options": [].

Ответ должен быть СТРОГО в формате JSON:
{
  "is_ambiguous": boolean,
  "reason": string or null,
  "options": [
    {
      "label": string,
      "text": string,
      "query": string
    }
  ]
}
"""


async def check_and_clarify_query(user_query: str) -> QueryClarification:
    """Analyze query with Ollama to check for ambiguity and return options."""
    settings = get_settings()

    headers = {}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"

    client = AsyncClient(host=settings.ollama_url, headers=headers)
    model_name = settings.ollama_text_model

    try:
        # Check if the desired model is loaded ONLY if using local Ollama (no api key).
        # Cloud LLM endpoints or LiteLLM proxies do not support/require client.list().
        if not settings.ollama_api_key:
            try:
                models_response = await client.list()
                available_models = [m.model for m in models_response.models]
                log.debug("query_clarification.ollama_models", available=available_models)

                if model_name not in available_models:
                    # If requested model not found, try to use vision model or whatever is available
                    if settings.ollama_vision_model in available_models:
                        model_name = settings.ollama_vision_model
                    elif available_models:
                        model_name = available_models[0]
            except Exception as list_err:
                log.warning("query_clarification.list_models_failed", error=str(list_err))
                # Keep default model name and proceed, maybe it's pulled automatically or cached

        response = await client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Проанализируй запрос: '{user_query}'"}
            ],
            format="json",
            options={
                "temperature": 0.1,
                "think": False
            }
        )

        content = response['message']['content']
        parsed_response = json.loads(content)

        # Parse into strongly typed QueryClarification
        options = []
        for opt in parsed_response.get("options", []):
            options.append(ClarificationOption(
                label=opt.get("label", ""),
                text=opt.get("text", ""),
                query=opt.get("query", "")
            ))

        return QueryClarification(
            is_ambiguous=bool(parsed_response.get("is_ambiguous", False)),
            reason=parsed_response.get("reason"),
            options=options
        )

    except Exception as e:
        log.warning("query_clarification.failed", query=user_query, error=str(e))
        return QueryClarification(
            is_ambiguous=False,
            reason=None,
            options=[]
        )
