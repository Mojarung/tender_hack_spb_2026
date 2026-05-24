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
from urllib.parse import quote_plus, urlparse

import orjson
import structlog

from pricepulse.antibot.google_browser import get_google_browser
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


def _chars_from_jsonld(payload: dict[str, Any]) -> dict[str, str]:
    """Squeeze every safe attribute out of a Schema.org Product into a
    flat characteristics dict. Strings only — the frontend facets the
    dynamic ones automatically."""
    out: dict[str, str] = {}
    # Plain scalar fields most shops emit
    for src_key, label in [
        ("color", "Цвет"),
        ("material", "Материал"),
        ("model", "Модель"),
        ("mpn", "Артикул"),
        ("gtin", "Штрихкод"),
        ("sku", "SKU"),
        ("category", "Категория"),
    ]:
        v = payload.get(src_key)
        if isinstance(v, str) and v.strip():
            out[label] = v.strip()[:120]
    # Dimensions / weight
    if isinstance(payload.get("weight"), (str, int, float)):
        out["Вес"] = str(payload["weight"])[:40]
    # additionalProperty list — Schema.org's spec-table mechanism
    extras = payload.get("additionalProperty")
    if isinstance(extras, list):
        for prop in extras[:30]:
            if not isinstance(prop, dict):
                continue
            name = str(prop.get("name") or "").strip()
            value = prop.get("value")
            if isinstance(value, dict):
                value = value.get("name") or value.get("value")
            value = str(value or "").strip()
            if name and value and name not in out:
                out[name[:60]] = value[:120]
    # Availability + condition
    offers = payload.get("offers")
    offer_block = offers if isinstance(offers, dict) else (
        offers[0] if isinstance(offers, list) and offers and isinstance(offers[0], dict) else None
    )
    if isinstance(offer_block, dict):
        avail = offer_block.get("availability")
        if isinstance(avail, str):
            # Strip the Schema.org namespace prefix
            out["Наличие"] = avail.rsplit("/", 1)[-1]
        cond = offer_block.get("itemCondition")
        if isinstance(cond, str):
            out["Состояние"] = cond.rsplit("/", 1)[-1]
    return out


def _to_offer(url: str, payload: dict[str, Any]) -> ProductOffer | None:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    price = _price_from(payload)
    if price is None or price < Decimal(100):
        return None
    site = urlparse(url).netloc
    chars: dict[str, str] = {"Магазин": site}
    brand = _brand_from(payload)
    if brand:
        chars["Бренд"] = brand
    chars.update(_chars_from_jsonld(payload))
    # Description goes into chars (truncated) so the frontend modal can
    # show it as a regular characteristic row.
    desc = payload.get("description")
    if isinstance(desc, str) and desc.strip():
        chars["Описание"] = desc.strip()[:500]
    return ProductOffer(
        source=SourceKind.RUNET,
        name=name.strip(),
        price=price,
        currency="RUB",
        url=url,
        image=_image_from(payload),
        characteristics=chars,
        seller=site,
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


def _google_stub_to_offer(stub: dict[str, Any], query_for_fallback: str) -> ProductOffer | None:
    """Build a ProductOffer straight from a Google Shopping card.

    Google ships all the fields inline (title / price / seller / rating /
    image / characteristics) so no per-shop fetch is needed. URL is a
    Google search deep-link because the real merchant URL hides behind a
    trusted-click handler (see google_research/03-04 probes)."""
    title = (stub.get("title") or "").strip()
    price_raw = stub.get("price")
    if not title or not price_raw:
        return None
    try:
        price = Decimal(str(price_raw))
    except (ValueError, ArithmeticError):
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
    # Keep both http (real gstatic CDN) and data: URIs — model accepts str
    # so the inline base64 thumbnail renders on the frontend until lazy
    # load swaps in the gstatic version.
    image_str = image if isinstance(image, str) and image.startswith(("http", "data:image")) else None
    chars: dict[str, str] = {}
    if seller:
        chars["Магазин"] = seller
    extra = stub.get("chars") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(v, str) and v:
                chars[str(k)] = v
    # Google merchant URL is hidden; we link into a Google Shopping search
    # so the user lands on the same card and clicks through manually.
    url = (
        "https://www.google.com/search?tbm=shop&hl=ru&gl=ru&q="
        + quote_plus(f"{title} {seller or ''} купить".strip())
    )
    _ = query_for_fallback   # reserved for future query-aware URL templates
    try:
        return ProductOffer(
            source=SourceKind.RUNET,
            name=title,
            price=price,
            currency="RUB",
            url=url,
            image=image_str,
            seller=seller,
            characteristics=chars,
            rating=rating_f,
            reviews_count=reviews_int,
            fetched_at=datetime.now(tz=UTC),
            cached=False,
        )
    except Exception as exc:
        log.warning("runet.google_offer_validation_failed", title=title[:60], error=str(exc))
        return None


def _dedup_key(offer: ProductOffer) -> tuple[str, str]:
    """Two offers from different sub-engines collapse to one if same shop
    + same (case-folded) first-50-chars of title."""
    seller = (offer.seller or "").lower().strip()
    name = offer.name.lower().strip()[:50]
    return (seller, name)


class RunetScraper:
    """4th source — Google Shopping + Yandex SERP, deduped under one banner.

    Two parallel sub-engines:
      1. Google Shopping (``google_browser``): rich product cards (price,
         seller, rating, image, badges) inline — single browser pass, no
         per-shop fetch.
      2. Yandex SERP (``yandex_browser``): organic results, then JSON-LD
         enrichment per merchant URL via curl_cffi. Lower hit-rate but
         covers shops Google misses (re-store, biggeek …).

    Results are merged + deduped by (seller, title_first_50).
    """

    source: SourceKind = SourceKind.RUNET

    def __init__(
        self,
        *,
        timeout_s: float = 8.0,
        enrich_concurrency: int = 6,
    ) -> None:
        self._timeout = timeout_s
        self._enrich_concurrency = enrich_concurrency

    async def _yandex_subsearch(
        self, q: str, geo_q: str, limit: int,
    ) -> list[ProductOffer]:
        """Run the legacy Yandex SERP → JSON-LD enrichment path."""
        try:
            browser = await get_yandex_browser()
            serp = await browser.serp_search(geo_q)
        except Exception as exc:
            log.warning("runet.yandex_browser_failed", error=str(exc))
            return []
        if serp.get("error"):
            log.warning("runet.yandex_serp_error", error=serp["error"])
            return []
        seen_hosts: set[str] = set()
        stubs: list[dict[str, Any]] = []
        for r in serp.get("products") or []:
            url = r.get("url")
            if not isinstance(url, str) or _is_excluded(url):
                continue
            host = urlparse(url).netloc.lower()
            if host in seen_hosts:
                continue
            seen_hosts.add(host)
            stubs.append(r)
        if not stubs:
            return []
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:    # pragma: no cover
            return []
        sem = asyncio.Semaphore(self._enrich_concurrency)
        async def _bounded(stub: dict[str, Any], session: Any) -> ProductOffer | None:
            async with sem:
                return await _fetch_jsonld_offer(session, stub, self._timeout)
        async with AsyncSession(impersonate="chrome", timeout=self._timeout) as session:
            results = await asyncio.gather(
                *(_bounded(s, session) for s in stubs[: limit * 3]),
                return_exceptions=True,
            )
        out: list[ProductOffer] = []
        for r in results:
            if isinstance(r, BaseException) or r is None:
                continue
            out.append(r)
        log.info("runet.yandex_subok", returned=len(out))
        return out

    async def _google_subsearch(self, q: str) -> list[ProductOffer]:
        """Run the Google Shopping path. Single browser pass, no enrichment."""
        try:
            browser = await get_google_browser()
            serp = await browser.shopping_search(q)
        except Exception as exc:
            log.warning("runet.google_browser_failed", error=str(exc))
            return []
        if serp.get("error"):
            log.warning("runet.google_shopping_error", error=serp["error"])
            return []
        out: list[ProductOffer] = []
        for stub in serp.get("products") or []:
            offer = _google_stub_to_offer(stub, q)
            if offer is not None:
                out.append(offer)
        log.info("runet.google_subok", returned=len(out))
        return out

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

        # Region-aware Yandex query: for non-default regions append
        # "купить в <city>" so SERP returns regional shops.
        geo_q = _maybe_geo_query(q, region_id)

        with scrape_duration_seconds.labels(source=self.source.value).time():
            # Fan out to both sub-engines in parallel. Each returns offers
            # or an empty list on any failure — never raises.
            google_offers, yandex_offers = await asyncio.gather(
                self._google_subsearch(q),
                self._yandex_subsearch(q, geo_q, limit),
            )

            # Merge with dedup — Google's richer offers win when keys collide
            # (full image / characteristics / inline rating).
            seen: set[tuple[str, str]] = set()
            offers: list[ProductOffer] = []
            for o in (*google_offers, *yandex_offers):
                key = _dedup_key(o)
                if key in seen:
                    continue
                seen.add(key)
                offers.append(o)
                if on_offer is not None:
                    await on_offer(o)
                if len(offers) >= limit:
                    break

            scrape_requests_total.labels(
                source=self.source.value, outcome="ok", proxy_tier="browser",
            ).inc()
            scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
            log.info(
                "runet.ok",
                returned=len(offers),
                from_google=len(google_offers),
                from_yandex=len(yandex_offers),
            )
            return ScrapeResult(source=self.source, offers=offers)


__all__ = ["RunetScraper"]
