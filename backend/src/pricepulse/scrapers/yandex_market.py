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

import html as html_lib
import json
import os
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

_MARKET_HOST_MARKERS = (
    "market.yandex.ru",
    "m.market.yandex.ru",
    "yandex.ru/market",
)

_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
window.chrome = window.chrome || {runtime: {}};
""".strip()

_L1_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://yandex.ru/",
}

# ──────────────────────────────────────── L1 helpers ─────────────────────────

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
    price_raw = offers_node.get("price") or offers_node.get("lowPrice")
    url = p.get("url") or offers_node.get("url")
    if not (name and price_raw and url):
        return None
    try:
        price = Decimal(str(price_raw))
    except Exception:
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
        url=url,
        image=image,
        characteristics={
            "brand": brand_name,
            "rating_count": str((rating_node or {}).get("reviewCount") or ""),
        },
        seller=seller_name,
        rating=rating_value,
        fetched_at=datetime.now(tz=UTC),
    )


# ──────────────────────────────────────── L2 helpers ─────────────────────────
# Ported from the reference Playwright-based parser (yandex_market_parser.py).
# These functions are pure data-transform — no Playwright import needed.


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", html_lib.unescape(value)).strip()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        text = value.replace("\xa0", " ")
        m = re.search(r"(\d[\d\s.,]*)", text)
        if m:
            try:
                return float(re.sub(r"\s+", "", m.group(1)).replace(",", "."))
            except ValueError:
                pass
    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        f = _to_float(value)
        return int(f) if f is not None else None


def _parse_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not (s.startswith("{") or s.startswith("[")):
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            result.append(item)
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))
    return result


def _walk_values(value: Any) -> list[Any]:
    result: list[Any] = []
    stack = [value]
    while stack:
        item = stack.pop()
        result.append(item)
        if isinstance(item, dict):
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))
    return result


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, (int, float)):
        return _clean_text(str(value))
    if isinstance(value, dict):
        for key in ("text", "content", "value", "raw", "name", "title"):
            text = _extract_text(value.get(key))
            if text:
                return text
    return ""


def _looks_like_service(value: str) -> bool:
    return value.lower() in {"в корзину", "купить", "доставка", "отзывы"}


def _extract_title(value: dict[str, Any]) -> str:
    embedded = _extract_text(value.get("_title"))
    if embedded:
        return embedded
    for key in ("name", "title", "modelName", "offerName", "rawTitle"):
        text = _extract_text(value.get(key))
        if text and not _looks_like_service(text):
            return text
    titles = value.get("titles")
    if isinstance(titles, dict):
        for key in ("raw", "highlighted", "full", "snippet"):
            text = _extract_text(titles.get(key))
            if text:
                return text
    model = value.get("model")
    if isinstance(model, dict):
        text = _extract_title(model)
        if text:
            return text
    return ""


def _extract_price_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("valueFmt", "value", "rawValue", "price", "amount", "current", "lowPrice"):
            p = _extract_price_value(value.get(key))
            if p is not None:
                return p
        return _parse_market_price(_extract_text(value))
    return _parse_market_price(value)


def _parse_market_price(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        n = float(value)
        return n / 10_000_000 if n >= 10_000_000 else n
    if not isinstance(value, str):
        return None
    text = value.replace("\xa0", " ")
    m = re.search(r"(\d[\d\s.,]*)", text)
    if not m:
        return None
    try:
        n = float(re.sub(r"\s+", "", m.group(1)).replace(",", "."))
    except ValueError:
        return None
    return n / 10_000_000 if n >= 10_000_000 else n


def _extract_price(value: dict[str, Any]) -> float | None:
    children = value.get("children")
    if isinstance(children, dict):
        wl = children.get("wishlist")
        if isinstance(wl, dict):
            p = _extract_price_value(wl.get("price"))
            if p is not None:
                return p
    zone = value.get("zoneData")
    if isinstance(zone, dict):
        p = _extract_price_value(zone.get("price"))
        if p is not None:
            return p
    pci = value.get("pendingCartItem")
    if isinstance(pci, dict):
        p = _extract_price_value(pci.get("price"))
        if p is not None:
            return p
    ap = value.get("additionalPrices")
    if isinstance(ap, list):
        prices = []
        for item in ap:
            if isinstance(item, dict):
                p = _extract_price_value(item.get("priceValue") or item.get("value"))
                if p is not None:
                    prices.append(p)
        if prices:
            return min(prices)
    for key in ("price", "currentPrice", "minPrice", "defaultOfferPrice", "value", "rawValue", "priceValue", "discountPrice"):
        p = _extract_price_value(value.get(key))
        if p is not None:
            return p
    prices_node = value.get("prices")
    if isinstance(prices_node, dict):
        for key in ("value", "rawValue", "price", "current", "discount", "min"):
            p = _extract_price_value(prices_node.get(key))
            if p is not None:
                return p
    offers = value.get("offers")
    if isinstance(offers, dict):
        p = _extract_price_value(offers.get("price") or offers.get("lowPrice"))
        if p is not None:
            return p
    return None


def _normalize_market_url(value: str) -> str:
    if value.startswith("https://market.yandex.ru") or value.startswith("https://yandex.ru/market"):
        return value
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return "https://market.yandex.ru" + value
    return value


def _is_market_product_url(value: str) -> bool:
    return (
        "market.yandex.ru/product" in value
        or "market.yandex.ru/search" in value
        or "yandex.ru/market/product" in value
        or "/card/" in value
        or "/product--" in value
    )


def _extract_url(value: dict[str, Any]) -> str | None:
    for key in ("_url", "url", "link", "productUrl", "modelUrl", "canonicalUrl", "targetUrl"):
        raw = value.get(key)
        if isinstance(raw, str):
            return _normalize_market_url(raw)
    nav = value.get("navnode")
    if isinstance(nav, dict):
        return _extract_url(nav)
    return None


def _is_image_like(value: str) -> bool:
    return any(
        marker in value
        for marker in ("avatars.mds.yandex.net", "yastatic.net", "market.yandex.ru", ".jpg", ".jpeg", ".png", ".webp")
    )


def _extract_image_value(value: Any) -> str | None:
    if isinstance(value, str):
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("http") and _is_image_like(value):
            return value
        return None
    if isinstance(value, dict):
        for key in ("url", "src", "imageUrl", "orig", "original", "thumbnail"):
            img = _extract_image_value(value.get(key))
            if img:
                return img
    if isinstance(value, list):
        for item in value:
            img = _extract_image_value(item)
            if img:
                return img
    return None


def _extract_candidate_image(value: dict[str, Any]) -> str | None:
    for key in ("_image_url", "image", "imageUrl", "picture", "pictures", "photo", "photos", "thumbnail", "src"):
        img = _extract_image_value(value.get(key))
        if img:
            return img
    children = value.get("children")
    if isinstance(children, dict):
        wl = children.get("wishlist")
        if isinstance(wl, dict):
            img = _extract_candidate_image(wl)
            if img:
                return img
    return None


def _extract_id(value: dict[str, Any]) -> str | None:
    for key in ("skuId", "marketSku", "modelId", "oskuId", "offerId", "wareId", "id", "productId"):
        raw = value.get(key)
        if isinstance(raw, (str, int)) and str(raw):
            return str(raw)
    url = _extract_url(value) or ""
    m = re.search(r"/(?:product|product--)[^/]*/(\d+)", url) or re.search(r"[?&]sku=(\d+)", url)
    return m.group(1) if m else None


def _extract_brand(value: dict[str, Any]) -> str | None:
    brand = value.get("brand") or value.get("vendor")
    if isinstance(brand, dict):
        return _extract_text(brand.get("name") or brand.get("title")) or None
    return _extract_text(brand) or None


def _extract_rating(value: dict[str, Any]) -> float | None:
    r = _to_float(value.get("_rating"))
    if r is not None and 0 <= r <= 5:
        return r
    for key in ("rating", "ratingValue", "averageRating", "reviewsRating"):
        r = _to_float(value.get(key))
        if r is not None and 0 <= r <= 5:
            return r
    agg = value.get("aggregateRating")
    if isinstance(agg, dict):
        return _extract_rating(agg)
    return None


def _extract_reviews(value: dict[str, Any]) -> int | None:
    rv = _to_int(value.get("_reviews"))
    if rv is not None:
        return rv
    for key in ("reviews", "reviewsCount", "reviewCount", "ratingCount", "opinions"):
        rv = _to_int(value.get(key))
        if rv is not None:
            return rv
    agg = value.get("aggregateRating")
    if isinstance(agg, dict):
        return _extract_reviews(agg)
    return None


def _extract_characteristics(value: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("description", "snippet"):
        text = _extract_text(value.get(key))
        if text:
            result[key] = text
    specs = value.get("specs") or value.get("characteristics") or value.get("params")
    if isinstance(specs, list):
        for item in specs:
            if not isinstance(item, dict):
                continue
            name = _extract_text(item.get("name") or item.get("title"))
            val = _extract_text(item.get("value") or item.get("text"))
            if name and val:
                result[name] = val
    return result


def _looks_like_product(value: dict[str, Any]) -> bool:
    if not (_extract_title(value) and _extract_price(value) is not None):
        return False
    if _extract_url(value) or _extract_id(value):
        return True
    return _extract_candidate_image(value) is not None


def _split_articles(html: str) -> list[str]:
    articles = re.findall(r"<article\b.*?</article>", html, flags=re.DOTALL)
    return articles or [html]


def _extract_product_snippet_data(html: str) -> dict[str, Any] | None:
    m = re.search(r'data-zone-name="productSnippet"[^>]*data-zone-data="([^"]+)"', html, flags=re.DOTALL)
    if not m:
        return None
    raw = html_lib.unescape(m.group(1))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _extract_first_market_href(html: str) -> str | None:
    for m in re.finditer(r'href="([^"]+)"', html):
        href = html_lib.unescape(m.group(1))
        if "/card/" in href or "/product" in href:
            return _normalize_market_url(href)
    return None


def _extract_first_image(html: str) -> str | None:
    for m in re.finditer(r'(?:src|data-src)="([^"]+)"', html):
        img = _extract_image_value(html_lib.unescape(m.group(1)))
        if img:
            return img
    return None


def _extract_rating_from_html(html: str) -> tuple[float | None, int | None]:
    rating = None
    reviews = None
    rm = re.search(r"Рейтинг товара:\s*([0-9.,]+)\s+из\s+5", html)
    if rm:
        rating = _to_float(rm.group(1).replace(",", "."))
    rvm = re.search(r"Оценок:\s*\(([\d\s]+)\)", html)
    if rvm:
        reviews = _to_int(rvm.group(1))
    return rating, reviews


def _extract_html_candidates(html: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for article in _split_articles(html):
        snippet = _extract_product_snippet_data(article)
        if snippet is None:
            continue
        url = _extract_first_market_href(article)
        img = _extract_first_image(article)
        rating, reviews = _extract_rating_from_html(article)
        if url:
            snippet["_url"] = url
        if img:
            snippet["_image_url"] = img
        if rating is not None:
            snippet["_rating"] = rating
        if reviews is not None:
            snippet["_reviews"] = reviews
        candidates.append(snippet)
    return candidates


def extract_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    roots: list[Any] = [payload]
    for value in _walk_values(payload):
        if isinstance(value, str) and "productSnippet" in value:
            roots.extend(_extract_html_candidates(value))
        parsed = _parse_json(value)
        if isinstance(parsed, (dict, list)):
            roots.append(parsed)
    candidates: list[dict[str, Any]] = []
    for root in roots:
        for item in _walk_dicts(root):
            if _looks_like_product(item):
                candidates.append(item)
    return candidates


def _is_market_json_response_url(url: str, content_type: str, resource_type: str) -> bool:
    if not any(m in url for m in _MARKET_HOST_MARKERS):
        return False
    has_json = "application/json" in content_type or "+json" in content_type
    return has_json and resource_type in {"fetch", "xhr"}


def _candidate_to_offer(c: dict[str, Any], *, region_id: int = 213) -> ProductOffer | None:
    title = _extract_title(c)
    price_f = _extract_price(c)
    url = _extract_url(c)
    if not title or price_f is None:
        return None
    if url and not _is_market_product_url(url):
        return None
    sid = _extract_id(c)
    if not url and sid is None:
        return None
    try:
        price = Decimal(str(price_f))
    except Exception:
        return None
    brand = _extract_brand(c) or ""
    reviews = _extract_reviews(c)
    chars: dict[str, str] = _extract_characteristics(c)
    if brand:
        chars["brand"] = brand
    if reviews is not None:
        chars["feedbacks"] = str(reviews)
    offer_url = build_region_url(url, region_id=region_id) if url else build_search_url(title, region_id=region_id)
    return ProductOffer(
        source=SourceKind.YA_MARKET,
        name=title,
        price=price,
        url=offer_url,
        image=_extract_candidate_image(c),
        characteristics=chars,
        rating=_extract_rating(c),
        fetched_at=datetime.now(tz=UTC),
    )


def _deduplicate(offers: list[ProductOffer], limit: int) -> list[ProductOffer]:
    seen: set[str] = set()
    result: list[ProductOffer] = []
    for o in offers:
        key = str(o.url)
        if key in seen:
            continue
        seen.add(key)
        result.append(o)
        if len(result) >= limit:
            break
    return result


# ──────────────────────────────────────── scraper ────────────────────────────


class YandexMarketScraper:
    source: SourceKind = SourceKind.YA_MARKET

    def __init__(self, timeout_s: float = 15.0, browser_timeout_ms: int = 45000, browser_wait_ms: int = 5000) -> None:
        self._timeout = timeout_s
        self._browser_timeout_ms = browser_timeout_ms
        self._browser_wait_ms = browser_wait_ms

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        src = self.source.value
        with scrape_duration_seconds.labels(source=src).time():
            # L1: fast HTTP path
            l1 = await self._http_search(query, limit, region_id=region_id)
            if len(l1) >= 2:
                scrape_requests_total.labels(source=src, outcome="ok", proxy_tier="none").inc()
                scrape_offers_returned_total.labels(source=src).inc(len(l1))
                log.info("ya_market.l1_ok", returned=len(l1))
                offers = _deduplicate(l1, limit)
                if on_offer:
                    for o in offers:
                        await on_offer(o)
                return ScrapeResult(source=self.source, offers=offers)

            # L2: Playwright XHR interception
            try:
                l2 = await self._playwright_search(query, limit, region_id=region_id)
            except Exception as exc:
                log.warning("ya_market.l2_failed", error=str(exc))
                l2 = []

            offers = _deduplicate(l2 or l1, limit)
            outcome = "ok" if offers else "blocked"
            scrape_requests_total.labels(source=src, outcome=outcome, proxy_tier="none").inc()
            scrape_offers_returned_total.labels(source=src).inc(len(offers))
            log.info("ya_market.ok", returned=len(offers), via="l2" if l2 else "l1_fallback")
            if on_offer:
                for o in offers:
                    await on_offer(o)
            return ScrapeResult(
                source=self.source,
                offers=offers,
                error=None if offers else "ya_market: no offers found",
            )

    # ─────────────────────────────── L1 ──────────────────────────────────────

    async def _http_search(self, query: NormalizedQuery, limit: int, *, region_id: int = 213) -> list[ProductOffer]:
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            return []

        url = build_search_url(query.normalized or query.raw, region_id=region_id)
        try:
            async with AsyncSession(impersonate="chrome131", timeout=self._timeout) as s:
                resp = await s.get(url, headers=_L1_HEADERS, cookies=build_region_cookies(region_id=region_id))
        except Exception as exc:
            log.warning("ya_market.l1_fetch_failed", error=str(exc))
            return []

        if resp.status_code != 200 or "showcaptcha" in str(resp.url):
            return []

        offers: list[ProductOffer] = []
        for p in _walk_ldjson(_ldjson_blocks(resp.text)):
            offer = _to_offer(p, region_id=region_id)
            if offer is None:
                continue
            offers.append(offer)
            if len(offers) >= limit:
                break
        return offers

    # ─────────────────────────────── L2 ──────────────────────────────────────

    async def _playwright_search(
        self,
        query: NormalizedQuery,
        limit: int,
        *,
        region_id: int = 213,
    ) -> list[ProductOffer]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.warning("ya_market.playwright_not_installed")
            return []

        search_url = build_search_url(query.normalized or query.raw, region_id=region_id)
        payloads: list[dict[str, Any]] = []

        async with async_playwright() as pw:
            launch_kwargs: dict[str, Any] = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-sandbox",
                ],
            }
            executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
            if executable and os.path.exists(executable):
                launch_kwargs["executable_path"] = executable
            browser = await pw.chromium.launch(
                **launch_kwargs,
            )
            ctx = await browser.new_context(
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={"width": 1440, "height": 1000},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={"accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"},
            )
            await ctx.add_init_script(_INIT_SCRIPT)
            await ctx.add_cookies([
                {"name": name, "value": value, "domain": ".yandex.ru", "path": "/"}
                for name, value in build_region_cookies(region_id=region_id).items()
            ])
            page = await ctx.new_page()

            async def on_response(response: Any) -> None:
                try:
                    url_r = response.url
                    ct = response.headers.get("content-type", "")
                    rt = response.request.resource_type
                    if not _is_market_json_response_url(url_r, ct, rt):
                        return
                    payload = await response.json()
                    if isinstance(payload, dict):
                        payloads.append(payload)
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=self._browser_timeout_ms)
                await page.wait_for_timeout(self._browser_wait_ms)
                for _ in range(3):
                    await page.mouse.wheel(0, 1800)
                    await page.wait_for_timeout(700)
                html_content = await page.content()
            finally:
                await ctx.close()
                await browser.close()

        offers: list[ProductOffer] = []
        seen: set[str] = set()

        def _add(o: ProductOffer) -> None:
            key = str(o.url)
            if key not in seen:
                seen.add(key)
                offers.append(o)

        # From XHR JSON payloads
        for payload in payloads:
            for candidate in extract_candidates(payload):
                o = _candidate_to_offer(candidate, region_id=region_id)
                if o:
                    _add(o)

        # From HTML: JSON-LD blocks
        for block in _ldjson_blocks(html_content):
            for p in _walk_ldjson([block]):
                o = _to_offer(p, region_id=region_id)
                if o:
                    _add(o)

        # From HTML: data-zone-data="productSnippet"
        for candidate in _extract_html_candidates(html_content):
            o = _candidate_to_offer(candidate, region_id=region_id)
            if o:
                _add(o)

        log.info("ya_market.l2_ok", xhr_payloads=len(payloads), offers=len(offers))
        return offers[:limit]
