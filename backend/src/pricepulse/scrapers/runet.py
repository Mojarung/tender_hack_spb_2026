"""4th source — Runet via Yandex SERP scraping.

Pipeline per query:
  1. Open ``yandex.ru/search/?text=<q>`` in a persistent stealth
     browser (``antibot/yandex_browser.py``). Run an in-page extractor
     that pulls organic results Yandex itself flagged as e-commerce
     (cart icon, inline rating block). The browser stays alive between
     requests so SmartCaptcha rarely triggers.
  2. Filter out marketplaces we already cover by their own adapter
     (Wildberries / Ozon / Я.Маркет) so Runet is genuinely informal
     shops (re-store / dns-shop / citilink / biggeek / mvideo / …).
  3. Fan-out: for each candidate URL, ``curl_cffi`` with Chrome JA3
     fetches the page and parses Schema.org ``Product`` JSON-LD to
     extract price / image / brand. Offers without a parsed price are
     dropped — ProductOffer.price is required.

Earlier iterations used SearXNG instead of Yandex SERP. SearXNG never
returned rich snippets so we had to fetch every URL and check JSON-LD
blind. Yandex SERP gives us pre-ranked, product-flavoured URLs so the
hit rate per fetch is much higher.

Live-validated in ``runet_research/05_jsonld_enrichment.py``.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import orjson
import structlog

from pricepulse.antibot.yandex_browser import get_yandex_browser
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.observability.metrics import (
    scrape_duration_seconds,
    scrape_offers_returned_total,
    scrape_requests_total,
)
from pricepulse.scrapers.base import OnOffer, ScrapeResult

log = structlog.get_logger(__name__)

# Yandex `lr` region_id → city name in locative case ("купить в <X>").
# Default 213 (Москва) is intentionally absent: appending "купить в Москве"
# would NARROW results (Москва is already SERP's default geo); for the
# default region we just send the bare query.
# Covers the top-12 regions for which WB also has dest codes — overlap is
# intentional so the same 12 cities power both adapters.
_REGION_LOCATIVE: dict[int, str] = {
    2:   "Санкт-Петербурге",
    54:  "Екатеринбурге",       # Свердловская область
    65:  "Новосибирске",
    35:  "Краснодаре",
    43:  "Казани",              # Татарстан
    47:  "Нижнем Новгороде",
    39:  "Ростове-на-Дону",
    172: "Уфе",
    51:  "Самаре",
    193: "Воронеже",
    50:  "Перми",
}


def _maybe_geo_query(query: str, region_id: int) -> str:
    """Append "купить в <city>" for known non-default regions; otherwise
    return the query unchanged. Yandex SERP then favours regional shops
    (spb.dns-shop.ru, ekb.mvideo.ru, regional Apple-stores, etc.)."""
    city = _REGION_LOCATIVE.get(region_id)
    return f"{query} купить в {city}" if city else query


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
    """Pull every JSON-LD payload out of a page, recursively expanding
    container shapes (arrays, @graph, ItemList → itemListElement, mainEntity).

    Many shops nest the Product inside ItemList / ListItem wrappers, so a
    flat walk would miss them.
    """
    raw_blocks: list[Any] = []
    for match in _JSONLD_RE.finditer(html):
        try:
            raw_blocks.append(orjson.loads(match.group(1)))
        except orjson.JSONDecodeError:
            continue

    out: list[dict[str, Any]] = []
    stack: list[Any] = list(raw_blocks)
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
        out.append(node)
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


async def _fetch_jsonld_offer(
    session: Any, stub: dict[str, Any], timeout_s: float,
) -> ProductOffer | None:
    """Single-URL enrichment: GET the page, parse Schema.org Product,
    build a ProductOffer. Returns None on anti-bot wall / no JSON-LD /
    no parseable price.
    """
    url = stub["url"]
    try:
        page = await session.get(url, headers=_HEADERS, timeout=timeout_s)
    except Exception as exc:
        log.debug("runet.fetch_failed", url=url, error=str(exc))
        return None
    if page.status_code != 200:
        log.debug("runet.http_non_200", url=url, status=page.status_code)
        return None
    html = page.text if isinstance(page.text, str) else page.content.decode(
        "utf-8", errors="replace",
    )
    for payload in _walk_jsonld(html):
        if not _is_product(payload):
            continue
        offer = _to_offer(url, payload)
        if offer is None:
            continue
        # Merge SERP-side rating / reviews if JSON-LD didn't have them
        # (cheap to re-derive via model_copy because ProductOffer is frozen).
        update: dict[str, Any] = {}
        if offer.rating is None and stub.get("rating") is not None:
            update["rating"] = float(stub["rating"])
        if offer.reviews_count is None and stub.get("reviews_count") is not None:
            update["reviews_count"] = int(stub["reviews_count"])
        return offer.model_copy(update=update) if update else offer
    return None


class RunetScraper:
    """4th source — Yandex SERP → non-marketplace candidates → JSON-LD."""

    source: SourceKind = SourceKind.RUNET

    def __init__(
        self,
        *,
        timeout_s: float = 8.0,
        enrich_concurrency: int = 6,
    ) -> None:
        self._timeout = timeout_s
        self._enrich_concurrency = enrich_concurrency

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

        # Region-aware query: for non-default regions append "купить в <city>"
        # so SERP returns regional shops (ekb.mvideo.ru, spb.dns-shop.ru, …).
        geo_q = _maybe_geo_query(q, region_id)

        with scrape_duration_seconds.labels(source=self.source.value).time():
            # 1) URL discovery — Yandex SERP through the stealth browser
            try:
                browser = await get_yandex_browser()
                serp = await browser.serp_search(geo_q)
            except Exception as exc:
                scrape_requests_total.labels(
                    source=self.source.value, outcome="blocked", proxy_tier="browser",
                ).inc()
                log.warning("runet.yandex_browser_failed", error=str(exc))
                return ScrapeResult(
                    source=self.source, offers=[],
                    error=f"Yandex SERP unavailable: {exc}",
                )
            if serp.get("error"):
                return ScrapeResult(
                    source=self.source, offers=[],
                    error=f"Yandex SERP: {serp['error']}",
                )

            # Filter out marketplaces we cover natively + dedup by host
            seen_hosts: set[str] = set()
            stubs: list[dict[str, Any]] = []
            for r in serp.get("products") or []:
                url = r.get("url")
                if not isinstance(url, str):
                    continue
                if _is_excluded(url):
                    continue
                host = urlparse(url).netloc.lower()
                if host in seen_hosts:
                    continue
                seen_hosts.add(host)
                stubs.append(r)
            if not stubs:
                scrape_requests_total.labels(
                    source=self.source.value, outcome="ok", proxy_tier="browser",
                ).inc()
                log.info("runet.empty", reason="no non-marketplace SERP results")
                return ScrapeResult(source=self.source, offers=[])

            # 2) Enrichment — fan-out via curl_cffi (Chrome JA3) for JSON-LD.
            #    Each call may 403/429/no-jsonld — those silently drop.
            try:
                from curl_cffi.requests import AsyncSession
            except ImportError:    # pragma: no cover
                return ScrapeResult(
                    source=self.source, offers=[],
                    error="curl_cffi not installed",
                )

            sem = asyncio.Semaphore(self._enrich_concurrency)

            async def _bounded(stub: dict[str, Any], session: Any) -> ProductOffer | None:
                async with sem:
                    return await _fetch_jsonld_offer(session, stub, self._timeout)

            async with AsyncSession(
                impersonate="chrome", timeout=self._timeout,
            ) as session:
                results = await asyncio.gather(
                    *(_bounded(s, session) for s in stubs[: limit * 3]),
                    return_exceptions=True,
                )

            offers: list[ProductOffer] = []
            for r in results:
                if isinstance(r, BaseException):
                    continue
                if r is None:
                    continue
                offers.append(r)
                if on_offer is not None:
                    await on_offer(r)
                if len(offers) >= limit:
                    break

            scrape_requests_total.labels(
                source=self.source.value, outcome="ok", proxy_tier="browser",
            ).inc()
            scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
            log.info(
                "runet.ok",
                returned=len(offers), urls_tried=len(stubs),
            )
            return ScrapeResult(source=self.source, offers=offers)


__all__ = ["RunetScraper"]
