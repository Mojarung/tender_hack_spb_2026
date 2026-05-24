"""НМЦК Excel-export — Приложение №1 к 44-ФЗ.

POST /api/v1/nmck/export {query, max_per_source?, region_id?, quantity?}
  → application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
    (Excel-файл с расчётом НМЦК по 3-5 коммерческим предложениям).

Используется тот же SearchOrchestrator, что и обычный поиск, — даём
жюри workflow «один клик от запроса до готового приложения к закупке».
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pricepulse.analytics import nmck as nmck_mod
from pricepulse.api.cache import get_rate_limiter, get_search_cache
from pricepulse.orchestrator.search import SearchOrchestrator

router = APIRouter(prefix="/nmck", tags=["search"])


class NmckRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    max_per_source: int = Field(default=10, ge=3, le=30)
    region_id: int = Field(default=213, ge=1)
    quantity: int = Field(default=1, ge=1, le=10_000)


@router.post("/export")
async def export_xlsx(req: NmckRequest) -> Response:
    """Run a search, pick three+ cheapest unique-seller offers,
    return Приложение №1 as an .xlsx download."""
    orchestrator = SearchOrchestrator(
        cache=await get_search_cache(),
        limiter=await get_rate_limiter(),
    )
    _, groups, _, _ = await orchestrator.run(
        req.query, max_per_source=req.max_per_source, region_id=req.region_id,
    )
    flat_offers = [o for g in groups for o in g.offers]
    stats = nmck_mod.compute(flat_offers)
    if stats is None:
        raise HTTPException(
            422,
            f"Недостаточно коммерческих предложений для НМЦК — нужно ≥3, "
            f"нашлось {len(flat_offers)}",
        )
    blob = nmck_mod.to_excel(
        query=req.query, offers=flat_offers, stats=stats, quantity=req.quantity,
    )
    # Cyrillic in Content-Disposition needs RFC-5987 percent-encoding so
    # the HTTP header stays pure ASCII. Modern browsers + curl read
    # filename* and use it as the saved name.
    safe_q = quote(f"НМЦК_{req.query[:60].replace(' ', '_')}.xlsx")
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="nmck.xlsx"; filename*=UTF-8\'\'{safe_q}'
            ),
            "X-PricePulse-Offers": str(stats.n_offers),
            "X-PricePulse-CV": str(stats.cv_pct),
        },
    )
