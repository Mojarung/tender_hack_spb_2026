"""Agent tools — the shared toolbox for our FastMCP server AND the
in-process chatbot endpoint.

Every function here is async, returns a JSON-serialisable dict / list,
and constructs its own resources (Redis client, etc.) lazily. This
keeps the FastMCP server free of FastAPI Depends gymnastics and lets
the chat endpoint call the same code with zero adapters.

If you add a tool, also register it in `mcp_server.py` so external
agents (Claude Code, Cursor, ...) can call it via MCP.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog
from redis.asyncio import Redis

from pricepulse.analytics.price_history import PriceHistoryStore
from pricepulse.analytics.sentiment import (
    aggregate,
    classify_batch,
    empty_breakdown,
)
from pricepulse.analytics.sentiment import (
    is_available as sentiment_available,
)
from pricepulse.config import get_settings
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import RankedOffer, SourceGroup
from pricepulse.orchestrator.search import SearchOrchestrator
from pricepulse.scrapers.wb_feedbacks import fetch_wb_feedbacks

log = structlog.get_logger(__name__)


def _redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=False)


def _jsonify(value: Any) -> Any:
    """Cheap JSON-coercion for Pydantic + Decimal + datetime + dataclasses."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    return value


# ───────────────────────── tools (public API) ─────────────────────────


async def search_products(query: str, max_per_source: int = 5) -> dict:
    """Search for a product across Wildberries, Ozon, Yandex Market and the
    non-formalised Runet 4th source (self-hosted SearXNG + JSON-LD).
    Returns groups + top deals.

    Args:
        query: free-form product query in Russian or English
        max_per_source: how many offers to keep per source (default 5)
    """
    orchestrator = SearchOrchestrator()
    normalized, groups, top_deals = await orchestrator.run(
        query=query, max_per_source=max_per_source
    )
    return {
        "query": normalized.model_dump(),
        "groups": [_summarise_group(g) for g in groups],
        "top_deals": [_summarise_deal(d) for d in top_deals[:5]],
    }


async def get_top_deals(query: str, max_per_source: int = 5, top_k: int = 5) -> list[dict]:
    """Run a search and return only the Best-Deal-ranked top offers."""
    result = await search_products(query, max_per_source=max_per_source)
    return result["top_deals"][:top_k]


async def get_price_history(source: SourceKind, item_id: str, limit: int = 100) -> dict:
    """Return accumulated price points for a product over time.

    Args:
        source: marketplace, e.g. 'wb'
        item_id: source-native id (nm_id for WB)
        limit: max points to return, oldest-first
    """
    redis = _redis()
    try:
        store = PriceHistoryStore(redis)
        points = await store.get(source.value, item_id, limit=limit)
    finally:
        await redis.aclose()
    return {
        "source": source.value,
        "item_id": item_id,
        "count": len(points),
        "points": [
            {"ts": p.ts.isoformat(), "price": str(p.price)} for p in points
        ],
    }


async def get_reviews_sample(
    item_id: int,
    *,
    sample: int = 30,
    quote_per_label: int = 2,
) -> dict:
    """Fetch a sample of WB feedbacks for an `imt_id` and return them
    grouped by sentiment label with the most useful quotes."""
    feedbacks = await fetch_wb_feedbacks(item_id, limit=sample)
    if not feedbacks:
        return {
            "item_id": item_id,
            "feedbacks_seen": 0,
            "available": sentiment_available(),
            "breakdown": asdict(empty_breakdown()),
            "quotes": {"positive": [], "neutral": [], "negative": []},
        }

    redis = _redis()
    try:
        results = await classify_batch([fb.joined_text for fb in feedbacks], redis=redis)
    finally:
        await redis.aclose()

    buckets: dict[str, list[dict]] = {"positive": [], "neutral": [], "negative": []}
    for fb, res in zip(feedbacks, results, strict=False):
        text = fb.joined_text
        if not text:
            continue
        buckets[res.label].append({
            "text": text[:240],
            "rating": fb.rating,
            "votes_plus": fb.pluses,
            "score": round(res.score, 3),
            "created": fb.created,
        })
    for items in buckets.values():
        items.sort(key=lambda q: (-q["votes_plus"], -q["score"]))
    quotes = {label: items[:quote_per_label] for label, items in buckets.items()}

    return {
        "item_id": item_id,
        "feedbacks_seen": len(feedbacks),
        "available": sentiment_available(),
        "breakdown": asdict(
            aggregate(
                [r for r, t in zip(results, [fb.joined_text for fb in feedbacks],
                                   strict=True) if t.strip()]
            )
        ),
        "quotes": quotes,
    }


async def compare_offers(items: list[dict[str, str]]) -> dict:
    """Compare offers from different sources.

    Args:
        items: list of {"source": "wb"|"ozon"|..., "item_id": "..."} —
               currently looks them up in the local price-history store
               (so populates with whatever the orchestrator captured).
    """
    redis = _redis()
    try:
        store = PriceHistoryStore(redis)
        results = []
        for it in items:
            src = it.get("source") or ""
            item_id = it.get("item_id") or ""
            latest = await store.latest(src, item_id)
            results.append({
                "source": src,
                "item_id": item_id,
                "latest_price": str(latest.price) if latest else None,
                "latest_ts": latest.ts.isoformat() if latest else None,
            })
    finally:
        await redis.aclose()
    if not results:
        return {"items": [], "cheapest": None}
    priced = [r for r in results if r["latest_price"] is not None]
    cheapest = min(priced, key=lambda r: Decimal(r["latest_price"])) if priced else None
    return {"items": results, "cheapest": cheapest}


# ───────────────────────── helpers ─────────────────────────


def _summarise_group(g: SourceGroup) -> dict:
    return {
        "source": g.source.value,
        "count": g.count,
        "min_price": str(g.min_price) if g.min_price is not None else None,
        "error": g.error,
        "offers": [
            {
                "name": o.name,
                "price": str(o.price),
                "currency": o.currency,
                "url": str(o.url),
                "image": str(o.image) if o.image else None,
                "rating": o.rating,
                "seller": o.seller,
            }
            for o in g.offers
        ],
    }


def _summarise_deal(d: RankedOffer) -> dict:
    o = d.offer
    return {
        "rank": d.rank,
        "score": d.score,
        "source": o.source.value,
        "name": o.name,
        "price": str(o.price),
        "currency": o.currency,
        "url": str(o.url),
        "image": str(o.image) if o.image else None,
        "rating": o.rating,
    }


__all__ = [
    "compare_offers",
    "get_price_history",
    "get_reviews_sample",
    "get_top_deals",
    "search_products",
]
