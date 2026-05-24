import time

from fastapi import APIRouter

from pricepulse.api.cache import get_rate_limiter, get_search_cache
from pricepulse.domain.models import SearchRequest, SearchResponse
from pricepulse.orchestrator.search import SearchOrchestrator

router = APIRouter(prefix="/search", tags=["search"])


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
