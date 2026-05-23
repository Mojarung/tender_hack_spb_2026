"""Runet (4th, non-formalized) adapter — Firecrawl `/v2/search` + scrape.

The "non-fixed 4th source" requirement from the brief is satisfied by
letting Firecrawl pick fresh top-N URLs per query, then asking it to
extract structured offer data via JSON schema. Hosts already covered by
our dedicated adapters are filtered out.

Free-tier 500 credits/month covers ~100 hot queries; for the live demo
we route most traffic to the deterministic MegamarketScraper.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from pricepulse.config import get_settings
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.observability.metrics import (
    scrape_duration_seconds,
    scrape_offers_returned_total,
    scrape_requests_total,
)
from pricepulse.scrapers.base import OnOffer, ScrapeResult

log = structlog.get_logger(__name__)

EXCLUDED_HOSTS = {
    "wildberries.ru", "www.wildberries.ru",
    "ozon.ru", "www.ozon.ru",
    "market.yandex.ru",
    "megamarket.ru", "www.megamarket.ru",   # we already cover it deterministically
}

_OFFER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "price": {"type": "number"},
        "currency": {"type": "string"},
        "image": {"type": "string"},
        "url": {"type": "string"},
        "characteristics": {"type": "object"},
    },
    "required": ["name", "price", "url"],
}


def _excluded(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in EXCLUDED_HOSTS)


_PLACEHOLDER_NAMES = {"карточка товара", "product card", "товар", ""}


def _to_offer(data: dict[str, Any], source_url: str) -> ProductOffer | None:
    name = (data.get("name") or "").strip()
    raw_price = data.get("price")
    try:
        price = Decimal(str(raw_price))
    except (TypeError, ValueError):
        return None
    if not name or price <= 0 or name.lower() in _PLACEHOLDER_NAMES:
        return None
    # Drop obvious LLM-hallucinations: ridiculously cheap iPhone/Apple etc.
    # The brief targets the RU market; anything <500₽ on a phone/laptop is suspect.
    currency = (data.get("currency") or "RUB").upper()
    if currency != "RUB":
        return None  # ignore foreign-currency results — keeps comparison clean
    if price < Decimal(100):
        return None
    return ProductOffer(
        source=SourceKind.RUNET,
        name=name,
        price=price,
        currency="RUB",
        url=data.get("url") or source_url,
        image=data.get("image"),
        characteristics={
            "site": urlparse(source_url).hostname or "",
            **(data.get("characteristics") or {}),
        },
        seller=None,
        rating=None,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


class RunetScraper:
    source: SourceKind = SourceKind.RUNET

    def __init__(self, timeout_s: float = 30.0) -> None:
        self._timeout = timeout_s

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        settings = get_settings()
        if not settings.firecrawl_api_key:
            return ScrapeResult(
                source=self.source, offers=[],
                error="firecrawl_api_key not set — runet adapter disabled",
            )

        base = settings.firecrawl_url.rstrip("/") + "/v2"
        headers = {
            "Authorization": f"Bearer {settings.firecrawl_api_key}",
            "Content-Type": "application/json",
        }

        src = self.source.value
        with scrape_duration_seconds.labels(source=src).time():
            async with httpx.AsyncClient(headers=headers, timeout=self._timeout) as client:
                try:
                    search_resp = await client.post(
                        f"{base}/search",
                        json={
                            "query": f"{query.normalized or query.raw} купить цена",
                            "limit": max(limit * 3, 10),
                            "sources": [{"type": "web"}],
                            "scrapeOptions": {
                                "formats": [
                                    {
                                        "type": "json",
                                        "schema": _OFFER_SCHEMA,
                                        "prompt": (
                                            "Это карточка товара в интернет-магазине. "
                                            "Извлеки название (name), цену в рублях (price, integer), "
                                            "ссылку на товар (url), картинку (image)."
                                        ),
                                    }
                                ],
                                "onlyMainContent": True,
                            },
                        },
                    )
                    search_resp.raise_for_status()
                except httpx.HTTPError as exc:
                    scrape_requests_total.labels(
                        source=src, outcome="timeout", proxy_tier="firecrawl",
                    ).inc()
                    log.warning("runet.search_failed", error=str(exc))
                    return ScrapeResult(
                        source=self.source, offers=[],
                        error=f"firecrawl search failed: {exc}",
                    )

            body = search_resp.json()
            results = (body.get("data") or {}).get("web") or []
            offers: list[ProductOffer] = []
            for item in results:
                url = item.get("url") or ""
                if not url or _excluded(url):
                    continue
                payload = item.get("json") or {}
                if not payload:
                    continue
                offer = _to_offer(payload, source_url=url)
                if offer is None:
                    continue
                offers.append(offer)
                if on_offer is not None:
                    await on_offer(offer)
                if len(offers) >= limit:
                    break

            scrape_requests_total.labels(
                source=src, outcome="ok", proxy_tier="firecrawl",
            ).inc()
            scrape_offers_returned_total.labels(source=src).inc(len(offers))
            log.info("runet.ok", returned=len(offers), credits=body.get("creditsUsed"))
            return ScrapeResult(source=self.source, offers=offers)
