"""Megamarket (Sber) adapter — default candidate for the 4th source.

Free-mode strategy:
    POST https://megamarket.ru/api/mobile/v2/catalogService/catalog/search
    with a tiny JSON body. Megamarket does not blanket-block this endpoint
    in 2026, but it needs a warmed-up `mg_sid` cookie. We warm by hitting
    the homepage once via the same curl_cffi session.

If the API path changes (Megamarket renumbers /api/mobile/vN/...), it's
isolated here. The route remains active per github.com/xob0t/mmparser
(Jan 2025).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import orjson
import structlog

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.observability.metrics import (
    scrape_duration_seconds,
    scrape_offers_returned_total,
    scrape_requests_total,
)
from pricepulse.scrapers.base import OnOffer, ScrapeResult

log = structlog.get_logger(__name__)

_BASE = "https://megamarket.ru"
_SEARCH = f"{_BASE}/api/mobile/v2/catalogService/catalog/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; SM-S908B) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ru,en;q=0.9",
    "Origin": _BASE,
    "Referer": f"{_BASE}/",
    "Content-Type": "application/json",
}


def _to_offer(item: dict[str, Any]) -> ProductOffer | None:
    name = item.get("title") or item.get("name") or ""
    price_node = item.get("price") or {}
    final = price_node.get("final") or price_node.get("price") or item.get("finalPrice")
    if not name or final in (None, 0):
        return None
    goods_id = item.get("goodsId") or item.get("goods_id") or item.get("id")
    url = (
        item.get("webUrl")
        or item.get("url")
        or (f"{_BASE}/catalog/details/{goods_id}/" if goods_id else None)
    )
    if not url:
        return None
    if url.startswith("/"):
        url = f"{_BASE}{url}"

    image = None
    images = item.get("images")
    if isinstance(images, list) and images:
        image = images[0].get("url") if isinstance(images[0], dict) else images[0]
    if isinstance(image, str) and image.startswith("//"):
        image = f"https:{image}"

    rating_node = item.get("rating") or {}
    rating = None
    if isinstance(rating_node, dict):
        try:
            rating = float(rating_node.get("average"))
        except (TypeError, ValueError):
            rating = None
    elif isinstance(rating_node, (int, float)):
        rating = float(rating_node)

    seller = (
        (item.get("merchant") or {}).get("name")
        if isinstance(item.get("merchant"), dict)
        else None
    )

    return ProductOffer(
        source=SourceKind.RUNET,   # reported under the floating-source group
        name=name,
        price=Decimal(str(final)),
        currency="RUB",
        url=url,
        image=image,
        characteristics={
            "site": "megamarket.ru",
            "brand": item.get("brand") or "",
            "rating": f"{rating:.1f}" if rating else "",
        },
        seller=seller,
        rating=rating,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


class MegamarketScraper:
    """Used as the deterministic fallback when SearXNG-driven RunetScraper
    cannot reach Megamarket through Firecrawl. Or as a default 4th source
    when no SearXNG is configured.
    """

    source: SourceKind = SourceKind.RUNET

    def __init__(self, timeout_s: float = 10.0) -> None:
        self._timeout = timeout_s

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:   # pragma: no cover
            return ScrapeResult(source=self.source, offers=[], error="curl_cffi not installed")

        body = {
            "requestVersion": 11,
            "searchText": query.normalized or query.raw,
            "listingParams": {
                "priceFilter": {"isDiscountedOnly": False},
                "selectedFilters": [],
                "sorting": 0,
            },
            "page": 0,
            "auth": {"locationId": "50", "appPlatform": "WEB"},
        }

        src = "megamarket"  # reported as RUNET source group, distinguish in metrics

        with scrape_duration_seconds.labels(source=src).time():
            try:
                async with AsyncSession(impersonate="chrome131", timeout=self._timeout) as s:
                    # Warm-up: pick up mg_sid from the homepage.
                    await s.get(_BASE, headers=_HEADERS)
                    resp = await s.post(_SEARCH, headers=_HEADERS, data=orjson.dumps(body))
            except Exception as exc:
                scrape_requests_total.labels(source=src, outcome="timeout", proxy_tier="none").inc()
                log.warning("megamarket.fetch_failed", error=str(exc))
                return ScrapeResult(source=self.source, offers=[], error=f"mm fetch failed: {exc}")

            if resp.status_code != 200:
                outcome = "blocked" if resp.status_code in (403, 451) else "http_4xx"
                scrape_requests_total.labels(source=src, outcome=outcome, proxy_tier="none").inc()
                return ScrapeResult(
                    source=self.source, offers=[],
                    error=f"mm HTTP {resp.status_code}"
                )

            try:
                data = orjson.loads(resp.content)
            except orjson.JSONDecodeError:
                scrape_requests_total.labels(
                    source=src, outcome="http_5xx", proxy_tier="none",
                ).inc()
                return ScrapeResult(source=self.source, offers=[], error="mm non-json")

            items = ((data.get("data") or {}).get("catalogListing") or {}).get("items") or []
            offers: list[ProductOffer] = []
            for item in items[:limit]:
                o = _to_offer(item)
                if o is None:
                    continue
                offers.append(o)
                if on_offer is not None:
                    await on_offer(o)
            scrape_requests_total.labels(source=src, outcome="ok", proxy_tier="none").inc()
            scrape_offers_returned_total.labels(source=src).inc(len(offers))
            log.info("megamarket.ok", returned=len(offers))
            return ScrapeResult(source=self.source, offers=offers)
