"""Wildberries adapter — public `search.wb.ru/v18` endpoint.

Public JSON, no auth, no captcha. Returns clean structured data.
The only protection is rate-limit per IP (~5 RPS); we keep it low and
back off on 429. See backend/docs/anti-bot.md §5.1.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from pricepulse.analytics.price_history import PriceHistoryStore
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import DeliveryInfo, NormalizedQuery, ProductAttributes, ProductOffer
from pricepulse.enrichment.attributes import extract_attributes
from pricepulse.observability.metrics import (
    scrape_duration_seconds,
    scrape_offers_returned_total,
    scrape_requests_total,
)
from pricepulse.scrapers.base import OnOffer, ScrapeResult
from pricepulse.scrapers.wb_basket import image_url as wb_image_url

log = structlog.get_logger(__name__)

_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v18/search"
_GEO_URL = "https://user-geo-data.wildberries.ru/get-geo-info"
_DEFAULT_DEST = "-1257786"          # Moscow region, universal in 2026

_CITY_COORDS: dict[str, tuple[float, float, str]] = {
    "москва": (55.7558, 37.6176, "Москва"),
    "moscow": (55.7558, 37.6176, "Москва"),
    "санкт-петербург": (59.9386, 30.3141, "Санкт-Петербург"),
    "спб": (59.9386, 30.3141, "Санкт-Петербург"),
    "saint petersburg": (59.9386, 30.3141, "Санкт-Петербург"),
    "новосибирск": (55.0084, 82.9357, "Новосибирск"),
    "novosibirsk": (55.0084, 82.9357, "Новосибирск"),
}

_WB_SUBJECT_CATEGORY: dict[int, str] = {
    515: "smartphone",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}


@dataclass(frozen=True, slots=True)
class _DestInfo:
    dest: str
    city: str | None = None
    source: str = "default"


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


def _city_key(city: str) -> str:
    return re.sub(r"\s+", " ", city.strip().lower().replace("\u0451", "\u0435"))


def _dest_from_xinfo(value: str) -> str | None:
    match = re.search(r"(?:^|&)dest=([^&]+)", value)
    return match.group(1) if match else None


async def _resolve_dest(city: str | None, timeout_s: float) -> _DestInfo:
    if not city:
        return _DestInfo(dest=_DEFAULT_DEST)
    coords = _CITY_COORDS.get(_city_key(city))
    if coords is None:
        return _DestInfo(dest=_DEFAULT_DEST, city=city, source="default_unknown_city")
    lat, lon, canonical_city = coords
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=timeout_s) as client:
            resp = await client.get(
                _GEO_URL,
                params={"latitude": lat, "longitude": lon, "address": canonical_city},
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as exc:
        log.warning("wb.geo_failed", city=city, error=str(exc))
        return _DestInfo(dest=_DEFAULT_DEST, city=canonical_city, source="default_geo_failed")
    dest = _dest_from_xinfo(str(body.get("xinfo") or ""))
    return _DestInfo(
        dest=dest or _DEFAULT_DEST,
        city=str(body.get("address") or canonical_city),
        source="wb_geo" if dest else "default_geo_no_dest",
    )


def _first_size(raw: dict[str, Any]) -> dict[str, Any]:
    sizes = raw.get("sizes") or []
    return sizes[0] if sizes and isinstance(sizes[0], dict) else {}


def _first_stock(size: dict[str, Any]) -> dict[str, Any]:
    stocks = size.get("stocks") or []
    return stocks[0] if stocks and isinstance(stocks[0], dict) else {}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _attrs(raw: dict[str, Any], name: str, characteristics: dict[str, str]) -> ProductAttributes:
    attrs = extract_attributes(name, characteristics)
    subject_id = _int_or_none(raw.get("subjectId"))
    colors = raw.get("colors") or []
    color_names = [c.get("name") for c in colors if isinstance(c, dict) and c.get("name")]
    category = _WB_SUBJECT_CATEGORY.get(subject_id or 0) or attrs.category
    extra = {**attrs.extra}
    if subject_id is not None:
        extra["wb_subject_id"] = subject_id
    subject_parent = _int_or_none(raw.get("subjectParentId"))
    if subject_parent is not None:
        extra["wb_subject_parent_id"] = subject_parent
    return attrs.model_copy(update={
        "category": category,
        "brand": str(raw.get("brand") or attrs.brand or "").lower() or None,
        "color": attrs.color,
        "extra": extra,
        "raw": {**attrs.raw, "wb_colors": ", ".join(color_names)},
        "confidence": max(attrs.confidence, 0.7 if category or color_names else attrs.confidence),
    })


def _delivery(raw: dict[str, Any], dest: _DestInfo) -> DeliveryInfo | None:
    size = _first_size(raw)
    stock = _first_stock(size)
    wh = stock.get("wh") or size.get("wh") or raw.get("wh")
    dist = stock.get("dist") or size.get("dist") or raw.get("dist")
    eta_min = stock.get("time1") or size.get("time1") or raw.get("time1")
    eta_max = stock.get("time2") or size.get("time2") or raw.get("time2")
    qty = stock.get("qty") or raw.get("totalQuantity")
    if not any(v is not None for v in (wh, dist, eta_min, eta_max, qty)):
        return None
    return DeliveryInfo(
        city=dest.city,
        region_id=dest.dest,
        region_source=dest.source,
        warehouse_id=str(wh) if wh is not None else None,
        distance_marketplace=_int_or_none(dist),
        eta_min_hours=_int_or_none(eta_min),
        eta_max_hours=_int_or_none(eta_max),
        stock=_int_or_none(qty),
        confidence=0.9 if dest.source == "wb_geo" else 0.65,
    )


def _to_offer(raw: dict[str, Any], dest: _DestInfo | None = None) -> ProductOffer | None:
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
    size = _first_size(raw)
    stock = _first_stock(size)
    colors = raw.get("colors") or []
    color_names = [c.get("name") for c in colors if isinstance(c, dict) and c.get("name")]
    characteristics = {
        "brand": raw.get("brand", ""),
        "supplier": raw.get("supplier", ""),
        "rating": f"{rating:.1f}",
        "feedbacks": str(feedbacks),
        "supplier_rating": str(raw.get("supplierRating") or ""),
        "colors": ", ".join(str(c) for c in color_names),
        "subject_id": str(raw.get("subjectId") or ""),
        "subject_parent_id": str(raw.get("subjectParentId") or ""),
        "weight": str(raw.get("weight") or ""),
        "volume": str(raw.get("volume") or ""),
        "stock": str(stock.get("qty") or raw.get("totalQuantity") or ""),
        "warehouse_id": str(stock.get("wh") or size.get("wh") or raw.get("wh") or ""),
        "distance_marketplace": str(stock.get("dist") or size.get("dist") or raw.get("dist") or ""),
        "eta_min_hours": str(stock.get("time1") or size.get("time1") or raw.get("time1") or ""),
        "eta_max_hours": str(stock.get("time2") or size.get("time2") or raw.get("time2") or ""),
    }
    dest_info = dest or _DestInfo(dest=_DEFAULT_DEST)
    return ProductOffer(
        source=SourceKind.WB,
        name=name,
        price=price,
        currency="RUB",
        url=url,
        image=image,
        characteristics=characteristics,
        attributes=_attrs(raw, name, characteristics),
        delivery=_delivery(raw, dest_info),
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
        city: str | None = None,
    ) -> None:
        self._dest = dest
        self._timeout = timeout_s
        self._price_history = price_history
        self._city = city

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        dest = await _resolve_dest(self._city, self._timeout)
        params = _params(query.normalized or query.raw, page=1, dest=dest.dest)

        async def _fetch() -> httpx.Response:
            async with httpx.AsyncClient(
                http2=True,
                headers=_HEADERS,
                timeout=self._timeout,
            ) as client:
                resp = await client.get(_SEARCH_URL, params=params)
                if resp.status_code == 429:
                    raise httpx.HTTPStatusError("rate-limited", request=resp.request, response=resp)
                resp.raise_for_status()
                return resp

        outcome = "ok"
        with scrape_duration_seconds.labels(source=self.source.value).time():
            try:
                resp = None
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type(httpx.HTTPStatusError),
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
                offer = _to_offer(raw, dest=dest)
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
    print(json.dumps(
        [o.model_dump(mode="json") for o in result.offers],
        ensure_ascii=False,
        indent=2,
    ))
