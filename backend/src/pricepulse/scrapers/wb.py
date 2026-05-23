"""Wildberries adapter — public `search.wb.ru/v18` endpoint.

Public JSON, no auth, no captcha. Returns clean structured data.
The only protection is rate-limit per IP (~5 RPS); we keep it low and
back off on 429.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from pricepulse.analytics.price_history import PriceHistoryStore
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.observability.metrics import (
    scrape_duration_seconds,
    scrape_offers_returned_total,
    scrape_requests_total,
)
from pricepulse.scrapers.base import OnOffer, ScrapeResult
from pricepulse.scrapers.wb_basket import image_url as wb_image_url

log = structlog.get_logger(__name__)

_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v18/search"
_DEFAULT_DEST = "-1257786"          # Moscow region, universal in 2026
_COOLDOWN_S = 120.0

_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


def _params(query: str, page: int, dest: str) -> dict[str, str]:
    return {
        "ab_testid": "false",
        "appType": "1",
        "curr": "rub",
        "dest": dest,
        "hide_dtype": "13",
        "lang": "ru",
        "page": str(page),
        "query": query,
        "resultset": "catalog",
        "sort": "popular",
        "spp": "30",
        "suppressSpellcheck": "false",
    }


def _price_from_sizes(sizes: list[dict[str, Any]]) -> Decimal | None:
    """WB v18 stores prices in kopeyki inside sizes[0].price.total."""
    if not sizes:
        return None
    price = sizes[0].get("price") or {}
    total = price.get("total") or price.get("product") or price.get("basic")
    if total is None:
        return None
    return Decimal(int(total)) / Decimal(100)   # kopeyki → rubles


def _to_offer(raw: dict[str, Any]) -> ProductOffer | None:
    nm_id = raw.get("id")
    name = raw.get("name") or ""
    if not nm_id or not name:
        return None
    price = _price_from_sizes(raw.get("sizes") or [])
    if price is None:
        return None
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    image = wb_image_url(int(nm_id))
    feedbacks = int(raw.get("feedbacks") or raw.get("nmFeedbacks") or 0)
    rating = float(raw.get("nmReviewRating") or raw.get("reviewRating") or raw.get("rating") or 0)
    return ProductOffer(
        source=SourceKind.WB,
        name=name,
        price=price,
        currency="RUB",
        url=url,
        image=image,
        characteristics={
            "brand": raw.get("brand", ""),
            "supplier": raw.get("supplier", ""),
            "rating": f"{rating:.1f}",
            "feedbacks": str(feedbacks),
        },
        seller=raw.get("supplier"),
        rating=rating if rating else None,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


class WildberriesScraper:
    source: SourceKind = SourceKind.WB

    def __init__(
        self,
        dest: str = _DEFAULT_DEST,
        timeout_s: float = 10.0,
        price_history: PriceHistoryStore | None = None,
    ) -> None:
        self._dest = dest
        self._timeout = timeout_s
        self._price_history = price_history
        self._cooldown_until = 0.0

    def _cooldown_left(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    def _open_cooldown(self, response: httpx.Response) -> float:
        retry_after = response.headers.get("retry-after")
        try:
            cooldown_s = max(_COOLDOWN_S, float(retry_after)) if retry_after else _COOLDOWN_S
        except ValueError:
            cooldown_s = _COOLDOWN_S
        self._cooldown_until = time.monotonic() + cooldown_s
        return cooldown_s

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        cooldown_left = self._cooldown_left()
        if cooldown_left > 0:
            return ScrapeResult(
                source=self.source,
                offers=[],
                error=f"wb cooldown active for {int(cooldown_left)}s after rate limit",
            )
        params = _params(query.normalized or query.raw, page=1, dest=self._dest)

        async def _fetch() -> httpx.Response:
            async with httpx.AsyncClient(
                http2=True,
                headers=_HEADERS,
                timeout=self._timeout,
            ) as client:
                resp = await client.get(_SEARCH_URL, params=params)
                if resp.status_code == 429:
                    cooldown_s = self._open_cooldown(resp)
                    raise httpx.HTTPStatusError(
                        f"rate-limited; cooldown={int(cooldown_s)}s",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp

        outcome = "ok"
        with scrape_duration_seconds.labels(source=self.source.value).time():
            try:
                resp = None
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception(
                        lambda exc: (
                            isinstance(exc, httpx.HTTPStatusError)
                            and exc.response.status_code != 429
                        )
                    ),
                    stop=stop_after_attempt(3),
                    wait=wait_exponential_jitter(initial=1, max=8),
                    reraise=True,
                ):
                    with attempt:
                        resp = await _fetch()
                assert resp is not None
            except httpx.HTTPError as exc:
                outcome = "http_4xx" if isinstance(exc, httpx.HTTPStatusError) else "timeout"
                scrape_requests_total.labels(
                    source=self.source.value, outcome=outcome, proxy_tier="none",
                ).inc()
                log.warning("wb.fetch_failed", error=str(exc))
                return ScrapeResult(source=self.source, offers=[], error=f"wb fetch failed: {exc}")

            body = resp.json()
            # WB v18 places `products` at the top level; older API versions had
            # `data.products`. Accept both for forward/backward compatibility.
            products = body.get("products") or (body.get("data") or {}).get("products") or []
            offers: list[ProductOffer] = []
            for raw in products[:limit]:
                offer = _to_offer(raw)
                if offer is None:
                    continue
                offers.append(offer)
                # Capture price-history point. Item id is the WB nm_id, parsed from URL.
                if self._price_history is not None:
                    nm_id = str(raw.get("id") or "")
                    if nm_id:
                        await self._price_history.record(self.source.value, nm_id, offer.price)
                if on_offer is not None:
                    await on_offer(offer)

            scrape_requests_total.labels(
                source=self.source.value, outcome=outcome, proxy_tier="none",
            ).inc()
            scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
            log.info("wb.ok", returned=len(offers), requested=limit)
            return ScrapeResult(source=self.source, offers=offers)


# Keep module-level coroutine helper for arq tasks
async def wb_search(query: str, limit: int = 10) -> ScrapeResult:
    nq = NormalizedQuery(raw=query, normalized=query, expansions=[])
    return await WildberriesScraper().search(nq, limit=limit)


# Convenience for quick scripts
if __name__ == "__main__":  # pragma: no cover
    import json

    result = asyncio.run(wb_search("iphone 15 128", limit=5))
    print(json.dumps([o.model_dump(mode="json") for o in result.offers], ensure_ascii=False, indent=2))
