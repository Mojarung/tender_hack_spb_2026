"""Wildberries adapter — DOM-scrape SSR + parallel basket-CDN enrichment.

Why DOM-scrape and not the v18 JSON API any more?
    `search.wb.ru/v18` is behind WB Page Guard (PG-41: TLS check;
    PG-42: per-request PoW token computed by the SPA bundle). HTTP-only
    clients — even with warmed cookies — get a 429 every time. WB
    Catalog is SSR (Nuxt), so navigating wildberries.ru/catalog/...
    yields fully-populated HTML. We let the browser do the nav and
    scrape products from the SSR'd page.

Pipeline per query:

    WBBrowserSearch.dom_search(query)
        → up to LIMIT product stubs (nm_id, name, price, image, brand,
          rating, feedbacks count, root=imt_id)
    For each stub, in parallel via asyncio.gather:
        ├─ wb_card.fetch_card(nm_id)   → chars + gallery + description
        │                                + imt_id + price_rub backfill
        └─ wb_feedbacks.fetch_wb_feedbacks(imt_id, limit=N)
                                       → reviews with photo_urls +
                                         video_urls + total counts

Total round-trip ~3-4 s steady-state (~6-8 s cold including browser
boot). Live-validated end-to-end in `wb_research/16_full_pipeline_v2.py`.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog

from pricepulse.analytics.price_history import PriceHistoryStore
from pricepulse.antibot.wb_browser import get_wb_browser
from pricepulse.config import get_settings
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.observability.metrics import (
    scrape_duration_seconds,
    scrape_offers_returned_total,
    scrape_requests_total,
)
from pricepulse.scrapers.base import OnOffer, ScrapeResult
from pricepulse.scrapers.wb_card import WbCardDetail, fetch_card
from pricepulse.scrapers.wb_feedbacks import fetch_wb_feedbacks

log = structlog.get_logger(__name__)

# DOM-scrape sometimes pulls the brand element together with the name,
# producing strings that start with "/" or " / ". Strip up-front so the
# UI doesn't show "/ iPhone 15 128GB".
_LEADING_TRASH_RE = re.compile(r"^[/\\\s|·•·]+")


_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


# ---------------------------------------------------------------------------
# Stub normalisation (3 DOM sources → uniform dict)
# ---------------------------------------------------------------------------
def _price_from_stub(p: dict[str, Any]) -> int | None:
    """Best-effort price (rubles) from whichever extractor produced this
    stub. Returns None if no source carried a sane value — caller falls
    back to card.json's clientPriceU."""
    pr = p.get("price_rub")
    if isinstance(pr, (int, float)) and 0 < pr < 5_000_000:
        return int(pr)
    # Nuxt: sizes[0].price.total (kopeyki → rub)
    sizes = p.get("sizes") or []
    if sizes:
        sp = (sizes[0] or {}).get("price") or {}
        total = sp.get("total") or sp.get("product") or sp.get("basic")
        if isinstance(total, (int, float)) and 0 < total < 5_000_000_00:
            return int(total) // 100
    # JSON-LD: offers.price (already in rub)
    if isinstance(p.get("price"), (int, float)) and 0 < p["price"] < 5_000_000:
        return int(p["price"])
    return None


def _normalize_stub(raw: dict[str, Any]) -> dict[str, Any] | None:
    nm = raw.get("nm") or raw.get("id") or raw.get("sku")
    if nm is None:
        return None
    try:
        nm = int(nm)
    except (TypeError, ValueError):
        return None
    brand = raw.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    name = (raw.get("name") or raw.get("imt_name") or "").strip()
    name = _LEADING_TRASH_RE.sub("", name).strip()
    return {
        "nm_id":    nm,
        "imt_id":   raw.get("root"),
        "name":     name,
        "brand":    brand,
        "supplier": raw.get("supplier"),
        "price":    _price_from_stub(raw),
        "rating":   raw.get("nmReviewRating") or raw.get("reviewRating")
                   or raw.get("rating"),
        "feedbacks": raw.get("feedbacks") or raw.get("nmFeedbacks") or 0,
        "image":    raw.get("image"),
        "url":      raw.get("url") or f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
    }


# ---------------------------------------------------------------------------
# Enrichment: card.json + reviews in parallel per stub
# ---------------------------------------------------------------------------
async def _enrich_one(
    http: httpx.AsyncClient,
    stub: dict[str, Any],
    *,
    reviews_per_offer: int,
) -> dict[str, Any]:
    """Fan-out for one stub. Card first (we need imt_id for reviews if
    the stub didn't carry `root`), then reviews. card.json + reviews
    are independent in time once imt_id is known."""
    nm = stub["nm_id"]
    card: WbCardDetail | None = await fetch_card(nm, client=http)

    if card:
        # Backfill chars / gallery / description from card.json
        stub["characteristics_grouped"] = card.characteristics
        stub["gallery"] = card.gallery
        stub["description"] = card.description
        stub["photo_count"] = card.photo_count
        if not stub.get("brand") and card.brand:
            stub["brand"] = card.brand
        if not stub.get("imt_id") and card.imt_id:
            stub["imt_id"] = card.imt_id
        if not stub.get("price") and card.price_rub:
            stub["price"] = card.price_rub
        if not stub.get("name") and card.imt_name:
            stub["name"] = card.imt_name
        stub["category"] = card.category
        stub["category_root"] = card.category_root

    imt = stub.get("imt_id")
    if imt:
        try:
            page = await fetch_wb_feedbacks(int(imt), limit=reviews_per_offer)
        except Exception as exc:    # never fatal
            log.warning("wb.enrich.feedbacks_failed", nm=nm, error=str(exc))
        else:
            stub["reviews"] = [
                {
                    "author": "Аноним",
                    "score": fb.rating,
                    "text": fb.joined_text or fb.text,
                    "published_at": fb.created,
                    "photos": [p["full"] for p in fb.photo_urls if p.get("full")],
                    "video": fb.video_urls,
                }
                for fb in page.feedbacks
            ]
            stub["reviews_total"] = page.total
            if page.valuation:
                # Prefer feedbacks-page valuation — it's the authoritative
                # WB-computed average; the search-stub `rating` is stale.
                stub["rating"] = page.valuation
    return stub


# ---------------------------------------------------------------------------
# Stub → ProductOffer
# ---------------------------------------------------------------------------
def _stub_to_offer(stub: dict[str, Any]) -> ProductOffer | None:
    name = stub.get("name") or ""
    price_rub = stub.get("price")
    url = stub.get("url")
    if not (name and price_rub and url):
        return None
    chars_grouped: list[tuple[str, str, str]] = stub.get("characteristics_grouped") or []
    chars: dict[str, str] = {}
    for _group, attr_name, attr_value in chars_grouped:
        # Modal/card UI ignores the group for now; we just dedupe by name.
        chars.setdefault(attr_name, attr_value)
    if stub.get("brand"):
        chars.setdefault("brand", str(stub["brand"]))
    if stub.get("supplier"):
        chars.setdefault("supplier", str(stub["supplier"]))

    images: list[str] = []
    for u in stub.get("gallery") or []:
        if isinstance(u, str) and u not in images:
            images.append(u)
    # cover image first if not already there
    cover = stub.get("image")
    if isinstance(cover, str) and cover and cover not in images:
        images.insert(0, cover)

    rating = stub.get("rating")
    try:
        rating_f = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_f = None

    reviews_count = stub.get("reviews_total") or stub.get("feedbacks") or None
    try:
        reviews_count = int(reviews_count) if reviews_count is not None else None
    except (TypeError, ValueError):
        reviews_count = None

    try:
        return ProductOffer(
            source=SourceKind.WB,
            name=name,
            price=Decimal(int(price_rub)),
            currency="RUB",
            url=url,
            image=(cover or (images[0] if images else None)) or None,
            images=images,
            characteristics=chars,
            seller=stub.get("supplier"),
            rating=rating_f,
            reviews=stub.get("reviews") or [],
            reviews_count=reviews_count,
            fetched_at=datetime.now(tz=UTC),
            cached=False,
        )
    except Exception as exc:
        log.warning("wb.offer_validation_failed", nm=stub.get("nm_id"), error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Public scraper
# ---------------------------------------------------------------------------
class WildberriesScraper:
    source: SourceKind = SourceKind.WB

    def __init__(
        self,
        *,
        timeout_s: float = 12.0,
        price_history: PriceHistoryStore | None = None,
        reviews_per_offer: int | None = None,
        enrich_concurrency: int | None = None,
    ) -> None:
        s = get_settings()
        self._timeout = timeout_s
        self._price_history = price_history
        self._reviews_per_offer = (
            reviews_per_offer if reviews_per_offer is not None
            else s.wb_reviews_per_offer
        )
        self._enrich_concurrency = (
            enrich_concurrency if enrich_concurrency is not None
            else s.wb_enrich_concurrency
        )

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        text = query.normalized or query.raw

        # 1. DOM-search via persistent browser
        try:
            browser = await get_wb_browser()
            dom = await browser.dom_search(text)
        except Exception as exc:
            log.warning("wb.dom_search_failed", error=str(exc))
            return ScrapeResult(
                source=self.source, offers=[],
                error=f"wb browser search failed: {exc}",
            )
        if dom.get("error"):
            return ScrapeResult(
                source=self.source, offers=[],
                error=f"wb dom_search: {dom['error']}",
            )

        raws = (dom.get("products") or [])[:max(1, min(limit, 50))]
        stubs = [s for s in (_normalize_stub(p) for p in raws) if s][:limit]
        log.info(
            "wb.dom_search_ok",
            requested=limit, got=len(stubs), source=dom.get("source"),
        )
        if not stubs:
            scrape_requests_total.labels(
                source=self.source.value, outcome="ok", proxy_tier="browser",
            ).inc()
            return ScrapeResult(source=self.source, offers=[])

        # 2. Fan-out enrichment (card.json + feedbacks v2) — bound by sem
        sem = asyncio.Semaphore(max(1, self._enrich_concurrency))

        async def _bounded(stub: dict[str, Any], http: httpx.AsyncClient) -> dict[str, Any]:
            async with sem:
                return await _enrich_one(http, stub, reviews_per_offer=self._reviews_per_offer)

        with scrape_duration_seconds.labels(source=self.source.value).time():
            async with httpx.AsyncClient(
                http2=True, headers=_HTTP_HEADERS, timeout=self._timeout,
            ) as http:
                enriched = await asyncio.gather(
                    *(_bounded(s, http) for s in stubs),
                    return_exceptions=True,
                )

        offers: list[ProductOffer] = []
        for r in enriched:
            if isinstance(r, BaseException):
                log.warning(
                    "wb.enrich_crash",
                    error=repr(r),
                    error_type=type(r).__name__,
                    traceback="".join(
                        __import__("traceback").format_exception(
                            type(r), r, r.__traceback__,
                        )[-3:],
                    ),
                )
                continue
            offer = _stub_to_offer(r)
            if offer is None:
                continue
            offers.append(offer)
            # Price-history capture (drops silently if store is None)
            if self._price_history is not None:
                nm = str(r.get("nm_id") or "")
                if nm:
                    try:
                        await self._price_history.record(
                            self.source.value, nm, offer.price,
                        )
                    except Exception as exc:    # never fatal
                        log.debug("wb.price_history_failed", error=str(exc))
            if on_offer is not None:
                await on_offer(offer)

        scrape_requests_total.labels(
            source=self.source.value, outcome="ok", proxy_tier="browser",
        ).inc()
        scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
        log.info("wb.ok", returned=len(offers), requested=limit)
        return ScrapeResult(source=self.source, offers=offers)


# Keep the module-level coroutine helper for arq tasks / scripts
async def wb_search(query: str, limit: int = 10) -> ScrapeResult:
    nq = NormalizedQuery(raw=query, normalized=query, expansions=[])
    return await WildberriesScraper().search(nq, limit=limit)


__all__ = ["WildberriesScraper", "wb_search"]
