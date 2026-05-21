import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from pricepulse.orchestrator.search import SearchOrchestrator

router = APIRouter(prefix="/search", tags=["search"])


async def _event_stream(query: str, max_per_source: int) -> AsyncIterator[dict[str, str]]:
    orchestrator = SearchOrchestrator()
    async for event_type, payload in orchestrator.stream(
        query=query, max_per_source=max_per_source
    ):
        yield {"event": event_type, "data": json.dumps(payload, default=str, ensure_ascii=False)}
        # Cooperative checkpoint — lets uvicorn flush the chunk.
        await asyncio.sleep(0)


@router.get("/stream")
async def stream(
    query: str = Query(..., min_length=1),
    max_per_source: int = Query(10, ge=1, le=50),
) -> EventSourceResponse:
    return EventSourceResponse(_event_stream(query=query, max_per_source=max_per_source))
