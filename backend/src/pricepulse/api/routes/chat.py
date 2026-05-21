"""POST /api/v1/chat — talk to the in-process Gemma 4 agent.

The endpoint is intentionally simple-blocking (one HTTP request, one
JSON response) so the frontend stays trivial. Tool calls happen
server-side via the shared `pricepulse.agent.tools` toolbox; the
response includes a redacted `tool_calls` audit log for the UI.

Anonymous use is allowed — if the JWT is missing we still serve the
request but skip session persistence. Pass `session_id` to keep a
24-hour conversation history in Redis.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi_users.exceptions import InvalidPasswordException, UserNotExists
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from pricepulse.agent.chat import ChatEngine
from pricepulse.api.deps import SettingsDep
from pricepulse.auth.users import fastapi_users
from pricepulse.storage.models import User

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None,
        description="Stable id to keep the conversation history in Redis (24h TTL).",
    )
    max_tool_rounds: int = Field(default=4, ge=0, le=8)


class ToolCallTrace(BaseModel):
    name: str
    result_keys: object


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    rounds: int
    history_len: int


_optional_user = fastapi_users.current_user(optional=True)


async def _redis(settings: SettingsDep) -> Redis | None:
    """Best-effort: if Redis is down (local-dev, no docker), skip persistence."""
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        await client.aclose()
        return None
    return client


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    redis: Annotated[Redis | None, Depends(_redis)],
    user: Annotated[User | None, Depends(_optional_user)] = None,
) -> ChatResponse:
    session_id = payload.session_id or (
        f"user-{user.id}" if user is not None else f"anon-{uuid.uuid4()}"
    )

    engine = ChatEngine(redis=redis)
    try:
        result = await engine.turn(
            payload.message,
            session_id=session_id,
            max_tool_rounds=payload.max_tool_rounds,
        )
    except (InvalidPasswordException, UserNotExists) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"chat engine failed: {exc}") from exc

    return ChatResponse(
        reply=result["reply"],
        session_id=session_id,
        tool_calls=[ToolCallTrace(**t) for t in result.get("tool_calls", [])],
        rounds=result["rounds"],
        history_len=result["history_len"],
    )
