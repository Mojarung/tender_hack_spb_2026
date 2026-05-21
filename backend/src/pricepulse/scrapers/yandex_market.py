"""Yandex Market adapter — JSON-LD-first, Camoufox-fallback.

Free-mode strategy:
    1. curl_cffi (Chrome 131 impersonate) GET to /search?text=...
    2. Parse <script type="application/ld+json"> blocks for Schema.org
       Product / ItemList. This works as long as Yandex returns the page
       (no SmartCaptcha challenge).
    3. On 200 + empty JSON-LD or on captcha redirect → escalate via
       cascade router (out of scope for this adapter — orchestrator
       handles it).

See backend/docs/anti-bot.md §5.3.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urljoin

import orjson
import structlog

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
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


def _decode_price(node: Any) -> Decimal | None:
    if isinstance(node, dict):
        node = node.get("price") or node.get("lowPrice")
    if node is None:
        return None
    try:
        return Decimal(str(node))
    except (TypeError, ValueError):
        return None


def _is_product(obj: dict[str, Any]) -> bool:
    t = obj.get("@type")
    if isinstance(t, list):
        return "Product" in t
    return t == "Product"


def _walk_ldjson(blocks: list[Any]) -> list[dict[str, Any]]:
    """Flatten @graph / arrays into a list of Product dicts."""
    out: list[dict[str, Any]] = []
    stack = list(blocks)
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
            continue
        if not isinstance(item, dict):
            continue
        if "@graph" in item and isinstance(item["@graph"], list):
            stack.extend(item["@graph"])
            continue
        if _is_product(item):
            out.append(item)
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


def _to_offer(p: dict[str, Any]) -> ProductOffer | None:
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
    image_node = p.get("image")
    if isinstance(image_node, list):
        image_node = image_node[0] if image_node else None
    if isinstance(image_node, str) and image_node.startswith("//"):
        image_node = f"https:{image_node}"

    rating_value = None
    rating_node = p.get("aggregateRating") or {}
    if isinstance(rating_node, dict):
        try:
            rating_value = float(rating_node.get("ratingValue"))
        except (TypeError, ValueError):
            rating_value = None

    return ProductOffer(
        source=SourceKind.YA_MARKET,
        name=name,
        price=price,
        currency=offers_node.get("priceCurrency") or "RUB",
        url=url,
        image=image_node,
        characteristics={
            "brand": (p.get("brand") or {}).get("name", "") if isinstance(p.get("brand"), dict) else str(p.get("brand") or ""),
            "rating_count": str((rating_node or {}).get("reviewCount") or ""),
        },
        seller=offers_node.get("seller", {}).get("name") if isinstance(offers_node.get("seller"), dict) else None,
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
    ) -> ScrapeResult:
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:    # pragma: no cover
            return ScrapeResult(source=self.source, offers=[], error="curl_cffi not installed")

        url = f"{_BASE}/search?text={quote(query.normalized or query.raw)}"

        try:
            async with AsyncSession(impersonate="chrome131", timeout=self._timeout) as s:
                resp = await s.get(url, headers=_HEADERS)
        except Exception as exc:  # noqa: BLE001
            log.warning("ya_market.fetch_failed", error=str(exc))
            return ScrapeResult(
                source=self.source, offers=[],
                error=f"ya_market fetch failed: {exc}"
            )

        if resp.status_code != 200 or "showcaptcha" in resp.url:
            return ScrapeResult(
                source=self.source, offers=[],
                error=f"ya_market HTTP {resp.status_code} (likely SmartCaptcha)",
            )

        html = resp.text
        blocks = _ldjson_blocks(html)
        products = _walk_ldjson(blocks)
        offers: list[ProductOffer] = []
        for p in products:
            offer = _to_offer(p)
            if offer is None:
                continue
            offers.append(offer)
            if on_offer is not None:
                await on_offer(offer)
            if len(offers) >= limit:
                break
        log.info("ya_market.ok", returned=len(offers), ldjson_blocks=len(blocks))
        return ScrapeResult(source=self.source, offers=offers)
