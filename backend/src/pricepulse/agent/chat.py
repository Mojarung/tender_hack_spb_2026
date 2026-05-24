"""Chat engine — Gemma 4 (via Ollama) with native tool-calling against
the shared `pricepulse.agent.tools` toolbox.

Loop:
    1. Append user message to history.
    2. Send history + tool specs to Ollama `/api/chat` (non-streaming for
       tool-call detection — Ollama streaming doesn't reliably emit
       full tool-call payloads).
    3. If the model returned tool calls — dispatch them, append the
       results as `role=tool` messages, loop.
    4. When no more tool calls — return the final assistant message.

History persistence is opt-in (Redis): pass a `session_id` and we hydrate
and store the whole transcript under `chat:{session_id}` with a 24h TTL.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import structlog
from ollama import AsyncClient
from redis.asyncio import Redis

from pricepulse.agent import tools
from pricepulse.config import get_settings
from pricepulse.domain.enums import SourceKind

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "Ты — ассистент PricePulse. Помогаешь пользователю найти товар "
    "по лучшей цене на маркетплейсах Wildberries, Ozon, Яндекс Маркет и "
    "других магазинах Рунета. У тебя есть инструменты для поиска товаров, "
    "получения истории цен и анализа отзывов. Отвечай кратко и по делу, "
    "не выдумывай товары — всегда сначала вызови search_products. "
    "Цены указывай в рублях. Когда показываешь top-предложения — "
    "называй магазин и кликабельный URL."
)


# ───────────────────────── tool registry ─────────────────────────


@dataclass(slots=True)
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Any


def _tool_spec_for_ollama(t: ToolDef) -> dict:
    """Convert our ToolDef → Ollama `tools[]` schema (Chat Completions-like)."""
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        },
    }


TOOLS: dict[str, ToolDef] = {
    "search_products": ToolDef(
        name="search_products",
        description=(
            "Search for a product across Wildberries, Ozon, Yandex Market and a "
            "floating 4th source. Returns per-source counts + Best-Deal-ranked offers. "
            "Always call this BEFORE answering about prices."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Что искать на русском или английском"},
                "max_per_source": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
        handler=lambda kw: tools.search_products(
            kw["query"], max_per_source=int(kw.get("max_per_source", 5))
        ),
    ),
    "get_top_deals": ToolDef(
        name="get_top_deals",
        description="Best-Deal-ranked top offers for a query (lighter than search).",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
        handler=lambda kw: tools.get_top_deals(kw["query"], top_k=int(kw.get("top_k", 5))),
    ),
    "get_price_history": ToolDef(
        name="get_price_history",
        description="Accumulated price points for a product. source in {wb,ozon,ya_market,runet}.",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": [s.value for s in SourceKind]},
                "item_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            },
            "required": ["source", "item_id"],
        },
        handler=lambda kw: tools.get_price_history(
            SourceKind(kw["source"]),
            kw["item_id"],
            limit=int(kw.get("limit", 100)),
        ),
    ),
    "get_reviews_sample": ToolDef(
        name="get_reviews_sample",
        description=(
            "Fetch recent WB feedbacks for `imt_id` and return sentiment breakdown "
            "with sample quotes. imt_id is the WB product's `root` field."
        ),
        parameters={
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "WB imt_id"},
                "sample": {"type": "integer", "default": 30, "minimum": 5, "maximum": 200},
            },
            "required": ["item_id"],
        },
        handler=lambda kw: tools.get_reviews_sample(
            int(kw["item_id"]), sample=int(kw.get("sample", 30))
        ),
    ),
    "compare_offers": ToolDef(
        name="compare_offers",
        description="Compare latest captured prices of a list of items across sources.",
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "item_id": {"type": "string"},
                        },
                        "required": ["source", "item_id"],
                    },
                },
            },
            "required": ["items"],
        },
        handler=lambda kw: tools.compare_offers(kw["items"]),
    ),
}


async def _dispatch_tool_call(call: dict[str, Any]) -> dict:
    """Run one tool call and return a JSON-safe payload."""
    fn = (call.get("function") or {})
    name = fn.get("name")
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return {"error": f"bad arguments JSON: {args[:200]}"}
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return await spec.handler(args)
    except Exception as exc:
        log.warning("chat.tool_failed", tool=name, error=str(exc))
        return {"error": f"{name} failed: {exc}"}


# ───────────────────────── chat loop ─────────────────────────


@dataclass(slots=True)
class ChatMessage:
    role: str          # 'system' | 'user' | 'assistant' | 'tool'
    content: str
    tool_calls: list[dict] | None = None
    tool_name: str | None = None

    def to_ollama(self) -> dict:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_name:
            d["name"] = self.tool_name
        return d


class ChatEngine:
    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis
        settings = get_settings()
        # Gemma 4 is multimodal — same model handles text chat, vision
        # (image-to-query) and tool-calling. Keeping chat on it means
        # one model warm in the cloud account instead of two.
        self._model = settings.ollama_vision_model or settings.ollama_text_model
        # Use the ollama-python library's AsyncClient — it routes
        # cloud (ollama.com) and local (http://ollama:11434) endpoints
        # correctly, handles auth, retries, and chunked responses.
        # Doing this via raw httpx ended in 404 / 401 against the cloud.
        headers: dict[str, str] = {}
        if settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
        self._client = AsyncClient(host=settings.ollama_url, headers=headers)

    async def _load_history(self, session_id: str | None) -> list[ChatMessage]:
        if not session_id or self._redis is None:
            return [ChatMessage("system", _SYSTEM_PROMPT)]
        raw = await self._redis.get(f"chat:{session_id}")
        if not raw:
            return [ChatMessage("system", _SYSTEM_PROMPT)]
        items = json.loads(raw)
        return [ChatMessage(**i) for i in items]

    async def _save_history(self, session_id: str | None, history: list[ChatMessage]) -> None:
        if not session_id or self._redis is None:
            return
        payload = json.dumps(
            [{"role": m.role, "content": m.content,
              "tool_calls": m.tool_calls, "tool_name": m.tool_name} for m in history],
            ensure_ascii=False,
        )
        await self._redis.set(f"chat:{session_id}", payload, ex=24 * 3600)

    async def turn(
        self,
        user_message: str,
        *,
        session_id: str | None = None,
        max_tool_rounds: int = 4,
    ) -> dict:
        """Run one full user turn — model may call tools multiple times."""
        history = await self._load_history(session_id)
        history.append(ChatMessage("user", user_message))

        tool_log: list[dict] = []
        tool_specs = [_tool_spec_for_ollama(t) for t in TOOLS.values()]

        for round_idx in range(max_tool_rounds + 1):
            response = await self._client.chat(
                model=self._model,
                messages=[m.to_ollama() for m in history],
                tools=tool_specs,
                stream=False,
                options={"temperature": 0.2, "think": False},
            )
            msg = response.get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""

            if not tool_calls:
                history.append(ChatMessage("assistant", content))
                await self._save_history(session_id, history)
                return {
                    "reply": content,
                    "tool_calls": tool_log,
                    "rounds": round_idx,
                    "history_len": len(history),
                }

            # Assistant requested tools — record + dispatch in parallel.
            history.append(ChatMessage("assistant", content, tool_calls=tool_calls))
            results = await asyncio.gather(*[_dispatch_tool_call(c) for c in tool_calls])
            for call, result in zip(tool_calls, results, strict=True):
                name = (call.get("function") or {}).get("name", "")
                tool_log.append({"name": name, "result_keys": _peek_keys(result)})
                history.append(ChatMessage(
                    role="tool",
                    content=json.dumps(result, ensure_ascii=False, default=str)[:8000],
                    tool_name=name,
                ))
        # Hit the round cap without a free-text answer
        history.append(ChatMessage("assistant", "(no further response after tool rounds)"))
        await self._save_history(session_id, history)
        return {
            "reply": "",
            "tool_calls": tool_log,
            "rounds": max_tool_rounds,
            "history_len": len(history),
            "truncated": True,
        }


def _peek_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return list(obj.keys())[:6]
    if isinstance(obj, list):
        return f"list[{len(obj)}]"
    return str(type(obj).__name__)
