import time

from fastapi import APIRouter

from pricepulse.domain.models import SearchRequest, SearchResponse
from pricepulse.orchestrator.search import SearchOrchestrator

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    started = time.perf_counter()
    orchestrator = SearchOrchestrator()
    normalized, groups, top_deals = await orchestrator.run(
        query=req.query,
        max_per_source=req.max_per_source,
        sources=req.sources,
        nofix=req.nofix,
        city=req.city,
    )
    took_ms = int((time.perf_counter() - started) * 1000)
    return SearchResponse(
        query=normalized,
        groups=groups,
        top_deals=top_deals,
        took_ms=took_ms,
    )
