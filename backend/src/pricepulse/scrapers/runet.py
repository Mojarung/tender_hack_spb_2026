"""4th source — non-formalised Runet, on our own infrastructure.

External search-engine APIs (Яндекс.Поиск, Google, Bing) are explicitly
banned by the methodology (final_presa.pdf, p.5), and so are external
scraping services (Firecrawl cloud, Scrapfly, etc.). The 4th source
therefore stands on tools we run ourselves:

  1. **SearXNG** (self-hosted meta-search, the ``searxng`` service in
     ``backend/docker-compose.yml``) discovers candidate URLs. SearXNG
     must have ``search.formats`` include ``json`` — see
     ``backend/searxng/settings.yml``.
  2. We filter the marketplaces already covered by WB / Ozon / Yandex
     Market adapters so the «4th source» is genuinely an informal Runet
     shop, not another marketplace (criterion 25/100).
  3. Each candidate URL is fetched with ``curl_cffi`` (Chrome
     impersonation) and parsed for a Schema.org ``Product`` block in
     ``<script type="application/ld+json">`` — most online stores
     publish one, so we get name + price + image + brand without
     site-specific code.

The 4th source is dynamic by construction: SearXNG re-queries the open
web for every search, so the actual shops change per query.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
import orjson
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

# Marketplaces already represented by other adapters — never count them
# as the "4th source", per methodology criterion 3 (25/100).
_EXCLUDED_HOSTS: frozenset[str] = frozenset({
    "wildberries.ru", "www.wildberries.ru",
    "ozon.ru", "www.ozon.ru",
    "market.yandex.ru", "ya.ru", "yandex.ru",
    "megamarket.ru", "sbermegamarket.ru",
    "aliexpress.ru", "aliexpress.com",
    "goods.ru",
    "lamoda.ru", "www.lamoda.ru",
})

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
}

_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _is_excluded(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == ex or host.endswith("." + ex) for ex in _EXCLUDED_HOSTS)


def _walk_jsonld(html: str) -> list[dict[str, Any]]:
    """Pull every JSON-LD payload out of a page, flattening @graph entries."""
    out: list[dict[str, Any]] = []
    for match in _JSONLD_RE.finditer(html):
        try:
            payload = orjson.loads(match.group(1))
        except orjson.JSONDecodeError:
            continue
        if isinstance(payload, list):
            out.extend(p for p in payload if isinstance(p, dict))
            continue
        if not isinstance(payload, dict):
            continue
        out.append(payload)
        graph = payload.get("@graph")
        if isinstance(graph, list):
            out.extend(g for g in graph if isinstance(g, dict))
    return out


def _is_product(payload: dict[str, Any]) -> bool:
    type_ = payload.get("@type")
    if isinstance(type_, list):
        return any(t == "Product" for t in type_)
    return type_ == "Product"


def _price_from(payload: dict[str, Any]) -> Decimal | None:
    """Pull a RUB price out of a Schema.org Product / Offer block."""
    candidates: list[Any] = []
    offers = payload.get("offers")
    if isinstance(offers, dict):
        candidates += [offers.get("price"), offers.get("lowPrice")]
    elif isinstance(offers, list):
        for off in offers:
            if isinstance(off, dict):
                candidates += [off.get("price"), off.get("lowPrice")]
    candidates.append(payload.get("price"))
    for c in candidates:
        if c is None:
            continue
        cleaned = re.sub(r"[^\d.]", "", str(c).replace(",", "."))
        if not cleaned:
            continue
        try:
            return Decimal(cleaned)
        except (ValueError, ArithmeticError):
            continue
    return None


def _image_from(payload: dict[str, Any]) -> str | None:
    image = payload.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    if isinstance(image, str) and image.startswith(("http://", "https://")):
        return image
    return None


def _brand_from(payload: dict[str, Any]) -> str:
    brand = payload.get("brand")
    if isinstance(brand, dict):
        return str(brand.get("name") or "")
    if isinstance(brand, str):
        return brand
    return ""


def _to_offer(url: str, payload: dict[str, Any]) -> ProductOffer | None:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    price = _price_from(payload)
    if price is None or price < Decimal(100):
        return None
    return ProductOffer(
        source=SourceKind.RUNET,
        name=name.strip(),
        price=price,
        currency="RUB",
        url=url,
        image=_image_from(payload),
        characteristics={
            "site": urlparse(url).netloc,
            "brand": _brand_from(payload),
        },
        seller=urlparse(url).netloc,
        rating=None,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


class RunetScraper:
    """4th source — SearXNG → top non-marketplace URLs → JSON-LD."""

    source: SourceKind = SourceKind.RUNET

    def __init__(self, timeout_s: float = 8.0, max_urls: int = 5) -> None:
        self._timeout = timeout_s
        self._max_urls = max_urls

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        searxng_url = get_settings().searxng_url.rstrip("/")
        q = (query.normalized or query.raw).strip()
        if not q:
            return ScrapeResult(source=self.source, offers=[])

        with scrape_duration_seconds.labels(source=self.source.value).time():
            # 1) URL discovery via self-hosted SearXNG.
            try:
                async with httpx.AsyncClient(
                    headers=_HEADERS, timeout=self._timeout, follow_redirects=True,
                ) as client:
                    resp = await client.get(
                        f"{searxng_url}/search",
                        params={
                            "q": f"{q} купить цена",
                            "format": "json",
                            "language": "ru-RU",
                            "categories": "general",
                            "safesearch": 0,
                        },
                    )
                    resp.raise_for_status()
                    sx_body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                scrape_requests_total.labels(
                    source=self.source.value, outcome="blocked", proxy_tier="none",
                ).inc()
                log.warning("runet.searxng_failed", error=str(exc))
                return ScrapeResult(
                    source=self.source, offers=[],
                    error=f"SearXNG unavailable: {exc}",
                )

            candidate_urls: list[str] = []
            for r in sx_body.get("results") or []:
                if not isinstance(r, dict):
                    continue
                url = r.get("url")
                if not isinstance(url, str) or _is_excluded(url):
                    continue
                candidate_urls.append(url)
                if len(candidate_urls) >= self._max_urls:
                    break

            if not candidate_urls:
                scrape_requests_total.labels(
                    source=self.source.value, outcome="ok", proxy_tier="none",
                ).inc()
                log.info("runet.empty", reason="no non-marketplace results")
                return ScrapeResult(source=self.source, offers=[])

            # 2) Fetch + JSON-LD extraction. curl_cffi is lazy-imported so
            #    bare-bones envs (CI without native libs) still load this module.
            try:
                from curl_cffi.requests import AsyncSession
            except ImportError:  # pragma: no cover
                return ScrapeResult(
                    source=self.source, offers=[],
                    error="curl_cffi not installed",
                )

            offers: list[ProductOffer] = []
            async with AsyncSession(impersonate="chrome", timeout=self._timeout) as session:
                for url in candidate_urls:
                    if len(offers) >= limit:
                        break
                    try:
                        page = await session.get(url, headers=_HEADERS)
                    except Exception as exc:
                        log.debug("runet.fetch_failed", url=url, error=str(exc))
                        continue
                    if page.status_code != 200:
                        continue
                    html = page.text if isinstance(page.text, str) else ""
                    for payload in _walk_jsonld(html):
                        if not _is_product(payload):
                            continue
                        offer = _to_offer(url, payload)
                        if offer is None:
                            continue
                        offers.append(offer)
                        if on_offer is not None:
                            await on_offer(offer)
                        break  # one product per page is enough

            scrape_requests_total.labels(
                source=self.source.value, outcome="ok", proxy_tier="none",
            ).inc()
            scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
            log.info(
                "runet.ok",
                returned=len(offers), urls_tried=len(candidate_urls),
            )
            return ScrapeResult(source=self.source, offers=offers)


__all__ = ["RunetScraper"]
