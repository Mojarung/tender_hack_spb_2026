"""Runet-source helpers — endpoints used by the frontend that aren't
in the main search flow. Right now: lazy URL resolution for Google
Shopping cards (they ship without a stable href; we click them on
demand to harvest the merchant URL).
"""

from __future__ import annotations

import hashlib

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pricepulse.antibot.google_browser import get_google_browser
from pricepulse.api.cache import get_search_cache

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/runet", tags=["search"])

# Cache resolved URLs for 24h — same product clicked twice in a session
# should be instant the second time.
_RESOLVE_TTL_S = 24 * 3600


class ResolveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200, description="Original search query")
    title: str = Field(..., min_length=1, max_length=400, description="Card title (used to find the right tile)")
    seller: str | None = Field(default=None, max_length=120)


class ResolveResponse(BaseModel):
    url: str | None


def _cache_key(req: ResolveRequest) -> str:
    raw = f"{req.query.strip().lower()}|{req.title.strip().lower()}|{(req.seller or '').strip().lower()}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()    # noqa: S324
    return f"runet:resolve:{h}"


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_url(req: ResolveRequest) -> ResolveResponse:
    """Lazy URL resolution — open Google Shopping, find the matching
    card, trusted-click it via CDP, return the resulting merchant URL.

    Cached for 24 h so repeat clicks are instant. Returns ``url: null``
    when the card couldn't be found or the click didn't navigate."""
    cache = await get_search_cache()
    key = _cache_key(req)
    if cache is not None:
        cached = await cache.get(key)
        if cached and isinstance(cached, dict) and cached.get("url"):
            return ResolveResponse(url=cached["url"])

    try:
        browser = await get_google_browser()
    except Exception as exc:
        log.warning("runet_resolve.browser_unavailable", error=str(exc))
        raise HTTPException(503, "browser unavailable") from exc

    url = await browser.resolve_card_url(
        query=req.query, title=req.title, seller=req.seller,
    )
    if cache is not None and url:
        await cache.set(key, {"url": url}, ttl_seconds=_RESOLVE_TTL_S)
    return ResolveResponse(url=url)
