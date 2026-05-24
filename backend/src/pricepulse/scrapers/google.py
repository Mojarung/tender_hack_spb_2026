"""5th source — Google Shopping (tbm=shop) via stealth browser.

Pipeline per query:
  1. Open ``google.com/search?q=<q>&tbm=shop&hl=ru&gl=ru`` in the
     persistent stealth browser (``antibot/google_browser.py``).
  2. Run an in-page extractor to pull product cards (title / price /
     seller / rating / reviews_count / image). All fields except URL
     come back inline — Google Shopping snippets are very rich.
  3. URL is currently a placeholder — Google fires the merchant URL via
     a trusted-click handler that ignores synthetic events (see
     ``google_research/`` probes 03-04). We fall back to a Google search
     deep-link for now so the user can still reach the product; a future
     iteration will lift the real URL via CDP mouse-click + new-tab
     capture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

import structlog

from pricepulse.antibot.google_browser import get_google_browser
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.observability.metrics import (
    scrape_duration_seconds,
    scrape_offers_returned_total,
    scrape_requests_total,
)
from pricepulse.scrapers.base import OnOffer, ScrapeResult

log = structlog.get_logger(__name__)


def _fallback_url(title: str, seller: str | None) -> str:
    """Deep-link into Google search until trusted-click capture lands."""
    q = f"{title} {seller or ''} купить".strip()
    return f"https://www.google.com/search?q={quote_plus(q)}&tbm=shop&hl=ru&gl=ru"


def _to_offer(stub: dict) -> ProductOffer | None:
    title = (stub.get("title") or "").strip()
    price_raw = stub.get("price")
    if not title or not price_raw:
        return None
    try:
        price = Decimal(str(price_raw))
    except (InvalidOperation, ValueError):
        return None
    if price < Decimal(100):
        return None
    seller = (stub.get("seller") or "").strip() or None
    rating = stub.get("rating")
    try:
        rating_f = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_f = None
    reviews_count = stub.get("reviews_count")
    try:
        reviews_int = int(reviews_count) if reviews_count is not None else None
    except (TypeError, ValueError):
        reviews_int = None
    image = stub.get("image")
    image_str = image if isinstance(image, str) and image.startswith("http") else None
    try:
        return ProductOffer(
            source=SourceKind.GOOGLE,
            name=title,
            price=price,
            currency="RUB",
            url=_fallback_url(title, seller),
            image=image_str,
            seller=seller,
            characteristics={"shop": seller} if seller else {},
            rating=rating_f,
            reviews_count=reviews_int,
            fetched_at=datetime.now(tz=UTC),
            cached=False,
        )
    except Exception as exc:
        log.warning("google.offer_validation_failed", title=title[:60], error=str(exc))
        return None


class GoogleScraper:
    """5th source — Google Shopping cards (no per-shop fetch needed)."""

    source: SourceKind = SourceKind.GOOGLE

    def __init__(self) -> None:
        pass

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        q = (query.normalized or query.raw).strip()
        if not q:
            return ScrapeResult(source=self.source, offers=[])

        with scrape_duration_seconds.labels(source=self.source.value).time():
            try:
                browser = await get_google_browser()
                serp = await browser.shopping_search(q)
            except Exception as exc:
                scrape_requests_total.labels(
                    source=self.source.value, outcome="blocked", proxy_tier="browser",
                ).inc()
                log.warning("google.browser_failed", error=str(exc))
                return ScrapeResult(
                    source=self.source, offers=[],
                    error=f"Google Shopping unavailable: {exc}",
                )
            if serp.get("error"):
                return ScrapeResult(
                    source=self.source, offers=[],
                    error=f"Google Shopping: {serp['error']}",
                )

            offers: list[ProductOffer] = []
            for stub in (serp.get("products") or [])[: limit * 2]:
                offer = _to_offer(stub)
                if offer is None:
                    continue
                offers.append(offer)
                if on_offer is not None:
                    await on_offer(offer)
                if len(offers) >= limit:
                    break

            scrape_requests_total.labels(
                source=self.source.value, outcome="ok", proxy_tier="browser",
            ).inc()
            scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
            log.info("google.ok", returned=len(offers))
            return ScrapeResult(source=self.source, offers=offers)


__all__ = ["GoogleScraper"]
