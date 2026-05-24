import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from pricepulse.api.cache import get_rate_limiter, get_search_cache
from pricepulse.domain.models import QueryClarification, SearchRequest, SearchResponse
from pricepulse.enrichment.query_clarification import check_and_clarify_query
from pricepulse.orchestrator.search import SearchOrchestrator

router = APIRouter(prefix="/search", tags=["search"])


class ClarifyRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=512)


@router.post("/clarify", response_model=QueryClarification)
async def clarify(req: ClarifyRequest) -> QueryClarification:
    """Pre-flight ambiguity check — the UI calls this BEFORE kicking
    off a full search so the user can pick one of the suggested
    interpretations instead of waiting for a doomed multi-source
    scrape. Returns is_ambiguous=false for normal queries → frontend
    proceeds straight to the stream."""
    return await check_and_clarify_query(req.query)


@router.post("", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    started = time.perf_counter()
    orchestrator = SearchOrchestrator(
        cache=await get_search_cache(),
        limiter=await get_rate_limiter(),
    )
    normalized, groups, top_deals, clarification = await orchestrator.run(
        query=req.query,
        max_per_source=req.max_per_source,
        sources=req.sources,
        region_id=req.region_id,
        nofix=req.nofix,
    )
    took_ms = int((time.perf_counter() - started) * 1000)
    return SearchResponse(
        query=normalized,
        groups=groups,
        top_deals=top_deals,
        clarification=clarification,
        took_ms=took_ms,
    )
