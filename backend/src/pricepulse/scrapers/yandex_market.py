"""Yandex Market adapter — JSON-LD-first, nodriver-fallback.

Strategy:
    1. curl_cffi (Chrome 131 impersonate) GET to /search?text=...
    2. Parse <script type="application/ld+json"> blocks for Schema.org
       Product / ItemList. This works as long as Yandex returns the page
       (no SmartCaptcha challenge).
    3. On 200 + empty JSON-LD or on captcha redirect → escalate via
       cascade router (out of scope for this adapter — orchestrator
       handles it).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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

_BASE = "https://market.yandex.ru"
_LDJSON_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.+?)</script>',
    flags=re.IGNORECASE | re.DOTALL,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://yandex.ru/",
}


def build_search_url(query: str, *, region_id: int = 213) -> str:
    params = urlencode({"text": query, "lr": region_id})
    return f"{_BASE}/search?{params}"


def build_region_url(url: str, *, region_id: int = 213) -> str:
    """Keep Yandex Market links reproducible when opened outside the scraper session."""
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params["lr"] = str(region_id)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


def build_region_cookies(*, region_id: int = 213) -> dict[str, str]:
    """Yandex primarily persists geo in cookies; `lr` alone is only a weak hint."""
    return {"yandex_gid": str(region_id), "gdpr": "0"}


def _decode_price(node: Any) -> Decimal | None:
    if isinstance(node, dict):
        node = node.get("price") or node.get("lowPrice")
    if node is None:
        return None
    try:
        return Decimal(str(node))
    except (TypeError, ValueError):
        return None


def _normalize_image_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return urljoin(_BASE, value)
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return None


def _extract_image(node: Any) -> str | None:
    if isinstance(node, str):
        return _normalize_image_url(node)
    if isinstance(node, list):
        for item in node:
            image = _extract_image(item)
            if image:
                return image
    if isinstance(node, dict):
        for key in ("url", "contentUrl", "thumbnailUrl", "image"):
            image = _extract_image(node.get(key))
            if image:
                return image
    return None


def _is_product(obj: dict[str, Any]) -> bool:
    t = obj.get("@type")
    if isinstance(t, list):
        return "Product" in t
    return t == "Product"


def _walk_ldjson(blocks: list[Any]) -> list[dict[str, Any]]:
    """Recursively flatten Schema.org container types into a list of Product dicts.

    Yandex Market SERPs wrap product cards in ItemList → ListItem → item, so a
    flat scan only sees the page-level "featured" Product (the «один товар»
    bug). Containers we descend into: arrays, @graph, mainEntity, ItemList's
    itemListElement (with or without ListItem wrappers).
    """
    out: list[dict[str, Any]] = []
    stack: list[Any] = list(blocks)
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        nid = id(node)
        if nid in seen:
            continue
        seen.add(nid)
        graph = node.get("@graph")
        if isinstance(graph, list):
            stack.extend(graph)
        ile = node.get("itemListElement")
        if isinstance(ile, list):
            for entry in ile:
                if isinstance(entry, dict):
                    inner = entry.get("item")
                    if isinstance(inner, (dict, list)):
                        stack.append(inner)
                    else:
                        stack.append(entry)
        me = node.get("mainEntity")
        if isinstance(me, (dict, list)):
            stack.append(me)
        if _is_product(node):
            out.append(node)
    return out


def _ldjson_blocks(html: str) -> list[Any]:
    out: list[Any] = []
    for match in _LDJSON_RE.finditer(html):
        raw = match.group(1).strip()
        try:
            out.append(orjson.loads(raw))
        except orjson.JSONDecodeError:
            continue
    return out


def _to_offer(p: dict[str, Any], *, region_id: int = 213) -> ProductOffer | None:
    name = p.get("name") or ""
    offers_node = p.get("offers") or {}
    if isinstance(offers_node, list):
        offers_node = offers_node[0] if offers_node else {}
    price = _decode_price(offers_node)
    url = p.get("url") or offers_node.get("url")
    if not (name and price and url):
        return None
    if url.startswith("/"):
        url = urljoin(_BASE, url)
    url = build_region_url(url, region_id=region_id)
    image = _extract_image(
        p.get("image")
        or p.get("thumbnailUrl")
        or offers_node.get("image")
        or offers_node.get("thumbnailUrl")
    )

    rating_value = None
    rating_node = p.get("aggregateRating") or {}
    if isinstance(rating_node, dict):
        try:
            rating_value = float(rating_node.get("ratingValue"))
        except (TypeError, ValueError):
            rating_value = None
    brand = p.get("brand")
    brand_name = brand.get("name", "") if isinstance(brand, dict) else str(brand or "")
    seller = offers_node.get("seller", {})
    seller_name = seller.get("name") if isinstance(seller, dict) else None

    return ProductOffer(
        source=SourceKind.YA_MARKET,
        name=name,
        price=price,
        currency=offers_node.get("priceCurrency") or "RUB",
        url=url,
        image=image,
        characteristics={
            "brand": brand_name,
            "rating_count": str((rating_node or {}).get("reviewCount") or ""),
        },
        seller=seller_name,
        rating=rating_value,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


class YandexMarketScraper:
    source: SourceKind = SourceKind.YA_MARKET

    def __init__(self, timeout_s: float = 15.0) -> None:
        self._timeout = timeout_s

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:    # pragma: no cover
            return ScrapeResult(source=self.source, offers=[], error="curl_cffi not installed")

        url = build_search_url(query.normalized or query.raw, region_id=region_id)
        src = self.source.value

        with scrape_duration_seconds.labels(source=src).time():
            try:
                async with AsyncSession(impersonate="chrome131", timeout=self._timeout) as s:
                    resp = await s.get(
                        url,
                        headers=_HEADERS,
                        cookies=build_region_cookies(region_id=region_id),
                    )
            except Exception as exc:
                scrape_requests_total.labels(source=src, outcome="timeout", proxy_tier="none").inc()
                log.warning("ya_market.fetch_failed", error=str(exc))
                return ScrapeResult(
                    source=self.source, offers=[],
                    error=f"ya_market fetch failed: {exc}"
                )

            captcha = "showcaptcha" in str(resp.url)
            if resp.status_code != 200 or captcha:
                outcome = "captcha" if captcha else "blocked"
                scrape_requests_total.labels(source=src, outcome=outcome, proxy_tier="none").inc()
                return ScrapeResult(
                    source=self.source, offers=[],
                    error=f"ya_market HTTP {resp.status_code} (likely SmartCaptcha)",
                )

            html = resp.text
            blocks = _ldjson_blocks(html)
            products = _walk_ldjson(blocks)
            offers: list[ProductOffer] = []
            for p in products:
                offer = _to_offer(p, region_id=region_id)
                if offer is None:
                    continue
                offers.append(offer)
                if on_offer is not None:
                    await on_offer(offer)
                if len(offers) >= limit:
                    break
            scrape_requests_total.labels(source=src, outcome="ok", proxy_tier="none").inc()
            scrape_offers_returned_total.labels(source=src).inc(len(offers))
            log.info("ya_market.ok", returned=len(offers), ldjson_blocks=len(blocks))
            return ScrapeResult(source=self.source, offers=offers)
