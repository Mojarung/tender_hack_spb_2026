"""Wildberries adapter — DOM-scrape SSR + parallel basket-CDN enrichment.

`search.wb.ru/v18` is now behind WB Page Guard (TLS + per-request PoW),
while the public catalog page remains SSR-rendered. The browser path gets
product stubs from wildberries.ru and enriches them with static basket CDN
card.json plus feedbacks v2.
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

from pricepulse.analytics.price_history import PriceHistoryStore
from pricepulse.antibot.wb_browser import get_wb_browser
from pricepulse.config import get_settings
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
from pricepulse.scrapers.wb_card import WbCardDetail, fetch_card
from pricepulse.scrapers.wb_feedbacks import fetch_wb_feedbacks

log = structlog.get_logger(__name__)

_GEO_URL = "https://user-geo-data.wildberries.ru/get-geo-info"
_DEFAULT_DEST = "-1257786"  # Moscow region, universal in 2026

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

_LEADING_TRASH_RE = re.compile(r"^[/\\\s|·•]+")

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


@dataclass(frozen=True, slots=True)
class _DestInfo:
    dest: str
    city: str | None = None
    source: str = "default"


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
        async with httpx.AsyncClient(headers=_HTTP_HEADERS, timeout=timeout_s) as client:
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


def _price_from_stub(p: dict[str, Any]) -> int | None:
    """Best-effort price (rubles) from any DOM extractor shape."""
    pr = p.get("price_rub")
    if isinstance(pr, (int, float)) and 0 < pr < 5_000_000:
        return int(pr)
    sizes = p.get("sizes") or []
    if sizes and isinstance(sizes[0], dict):
        sp = (sizes[0].get("price") or {}) if isinstance(sizes[0].get("price"), dict) else {}
        total = sp.get("total") or sp.get("product") or sp.get("basic")
        if isinstance(total, (int, float)) and 0 < total < 5_000_000_00:
            return int(total) // 100
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
    image = raw.get("image") or raw.get("img") or ""
    if not image:
        image = wb_image_url(nm)

    return {
        "nm_id": nm,
        "imt_id": raw.get("root") or raw.get("imt_id"),
        "name": name,
        "brand": brand,
        "supplier": raw.get("supplier"),
        "price": _price_from_stub(raw),
        "rating": raw.get("nmReviewRating") or raw.get("reviewRating") or raw.get("rating"),
        "feedbacks": raw.get("feedbacks") or raw.get("nmFeedbacks") or 0,
        "image": image,
        "url": raw.get("url") or f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
        "raw": raw,
    }


async def _enrich_one(
    http: httpx.AsyncClient,
    stub: dict[str, Any],
    *,
    reviews_per_offer: int,
) -> dict[str, Any]:
    """Fetch card.json and feedbacks for one product stub."""
    nm = stub["nm_id"]
    card: WbCardDetail | None = await fetch_card(nm, client=http)

    if card:
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
        if not stub.get("image") and card.gallery:
            stub["image"] = card.gallery[0]
        stub["category"] = card.category
        stub["category_root"] = card.category_root

    imt = stub.get("imt_id")
    if imt:
        try:
            page = await fetch_wb_feedbacks(int(imt), limit=reviews_per_offer)
        except Exception as exc:  # never fatal
            log.warning("wb.enrich.feedbacks_failed", nm=nm, error=str(exc))
        else:
            stub["reviews"] = [
                {
                    "author": "Аноним",
                    "score": fb.rating,
                    "text": fb.joined_text or fb.text,
                    "published_at": fb.created,
                    "photos": [p["full"] for p in fb.photo_urls if p.get("full")],
                    # `video` dropped: WB feedbacks v2 returns {preview, m3u8}
                    # dicts but ProductOffer.reviews values are str|int|list[str]
                    # |None — the dict fails Pydantic validation and silently
                    # drops the whole offer. UI doesn't render videos anyway.
                }
                for fb in page.feedbacks
            ]
            stub["reviews_total"] = page.total
            if page.valuation:
                stub["rating"] = page.valuation
    return stub


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


def _attrs(stub: dict[str, Any], name: str, characteristics: dict[str, str]) -> ProductAttributes:
    raw = stub.get("raw") if isinstance(stub.get("raw"), dict) else {}
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
    if stub.get("category"):
        extra["wb_category"] = str(stub["category"])
    if stub.get("category_root"):
        extra["wb_category_root"] = str(stub["category_root"])
    brand = str(stub.get("brand") or attrs.brand or "").lower() or None
    return attrs.model_copy(update={
        "category": category,
        "brand": brand,
        "extra": extra,
        "raw": {**attrs.raw, "wb_colors": ", ".join(color_names)},
        "confidence": max(attrs.confidence, 0.7 if category or color_names else attrs.confidence),
    })


def _delivery(stub: dict[str, Any], dest: _DestInfo) -> DeliveryInfo | None:
    raw = stub.get("raw") if isinstance(stub.get("raw"), dict) else {}
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


def _stub_to_offer(stub: dict[str, Any], dest: _DestInfo) -> ProductOffer | None:
    name = (stub.get("name") or "").strip()
    price_rub = stub.get("price")
    url = stub.get("url")
    if not (name and price_rub and url):
        return None

    chars_grouped: list[tuple[str, str, str]] = stub.get("characteristics_grouped") or []
    chars: dict[str, str] = {}
    for _group, attr_name, attr_value in chars_grouped:
        chars.setdefault(attr_name, attr_value)
    if stub.get("brand"):
        chars.setdefault("brand", str(stub["brand"]))
    if stub.get("supplier"):
        chars.setdefault("supplier", str(stub["supplier"]))

    images: list[str] = []
    for url_item in stub.get("gallery") or []:
        if isinstance(url_item, str) and url_item not in images:
            images.append(url_item)
    cover = stub.get("image")
    if isinstance(cover, str) and cover and cover not in images:
        images.insert(0, cover)

    rating = stub.get("rating")
    try:
        rating_f = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_f = None
    if rating_f:
        chars.setdefault("rating", f"{rating_f:.1f}")

    reviews_count = stub.get("reviews_total") or stub.get("feedbacks") or None
    try:
        reviews_count = int(reviews_count) if reviews_count is not None else None
    except (TypeError, ValueError):
        reviews_count = None
    if reviews_count is not None:
        chars.setdefault("feedbacks", str(reviews_count))

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
            attributes=_attrs(stub, name, chars),
            delivery=_delivery(stub, dest),
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


class WildberriesScraper:
    source: SourceKind = SourceKind.WB

    def __init__(
        self,
        *,
        timeout_s: float = 12.0,
        price_history: PriceHistoryStore | None = None,
        city: str | None = None,
        reviews_per_offer: int | None = None,
        enrich_concurrency: int | None = None,
    ) -> None:
        settings = get_settings()
        self._timeout = timeout_s
        self._price_history = price_history
        self._city = city
        self._reviews_per_offer = (
            reviews_per_offer if reviews_per_offer is not None else settings.wb_reviews_per_offer
        )
        self._enrich_concurrency = (
            enrich_concurrency if enrich_concurrency is not None else settings.wb_enrich_concurrency
        )

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        del region_id  # WB region support still uses city->dest when configured.
        text = query.normalized or query.raw
        dest = await _resolve_dest(self._city, self._timeout)

        with scrape_duration_seconds.labels(source=self.source.value).time():
            try:
                browser = await get_wb_browser()
                dom = await browser.dom_search(text)
            except Exception as exc:
                scrape_requests_total.labels(
                    source=self.source.value,
                    outcome="browser_error",
                    proxy_tier="browser",
                ).inc()
                log.warning("wb.dom_search_failed", error=str(exc))
                return ScrapeResult(source=self.source, offers=[], error=f"wb browser search failed: {exc}")

            if dom.get("error"):
                scrape_requests_total.labels(
                    source=self.source.value,
                    outcome="browser_error",
                    proxy_tier="browser",
                ).inc()
                return ScrapeResult(source=self.source, offers=[], error=f"wb dom_search: {dom['error']}")

            raws = (dom.get("products") or [])[: max(1, min(limit, 50))]
            stubs = [stub for stub in (_normalize_stub(p) for p in raws) if stub][:limit]
            log.info("wb.dom_search_ok", requested=limit, got=len(stubs), source=dom.get("source"))
            if not stubs:
                scrape_requests_total.labels(source=self.source.value, outcome="ok", proxy_tier="browser").inc()
                return ScrapeResult(source=self.source, offers=[])

            sem = asyncio.Semaphore(max(1, self._enrich_concurrency))

            async def _bounded(stub: dict[str, Any], http: httpx.AsyncClient) -> dict[str, Any]:
                async with sem:
                    return await _enrich_one(http, stub, reviews_per_offer=self._reviews_per_offer)

            async with httpx.AsyncClient(http2=True, headers=_HTTP_HEADERS, timeout=self._timeout) as http:
                enriched = await asyncio.gather(*(_bounded(s, http) for s in stubs), return_exceptions=True)

        offers: list[ProductOffer] = []
        for result in enriched:
            if isinstance(result, BaseException):
                log.warning("wb.enrich_crash", error=repr(result), error_type=type(result).__name__)
                continue
            offer = _stub_to_offer(result, dest)
            if offer is None:
                continue
            offers.append(offer)
            if self._price_history is not None:
                nm = str(result.get("nm_id") or "")
                if nm:
                    try:
                        await self._price_history.record(self.source.value, nm, offer.price)
                    except Exception as exc:  # never fatal
                        log.debug("wb.price_history_failed", error=str(exc))
            if on_offer is not None:
                await on_offer(offer)

        scrape_requests_total.labels(source=self.source.value, outcome="ok", proxy_tier="browser").inc()
        scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
        log.info("wb.ok", returned=len(offers), requested=limit)
        return ScrapeResult(source=self.source, offers=offers)


async def wb_search(query: str, limit: int = 10) -> ScrapeResult:
    nq = NormalizedQuery(raw=query, normalized=query, expansions=[])
    return await WildberriesScraper().search(nq, limit=limit)


__all__ = ["WildberriesScraper", "wb_search"]
