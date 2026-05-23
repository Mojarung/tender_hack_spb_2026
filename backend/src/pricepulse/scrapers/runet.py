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

import asyncio
import hashlib
import html as html_lib
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx
import orjson
import structlog

from pricepulse.api.cache import get_search_cache
from pricepulse.config import get_settings
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductAttributes, ProductOffer
from pricepulse.enrichment.attributes import (
    extract_offer_attributes,
    extract_query_attributes,
    is_attribute_conflict,
    merge_attributes,
)
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

# Russian-segment TLDs. Anything outside is rejected: foreign shops
# usually have foreign currency that we'd mis-tag as RUB.
_ALLOWED_TLDS: tuple[str, ...] = (
    ".ru", ".su", ".рф", ".xn--p1ai",  # .рф punycode
    ".by", ".kz",
)

# JSON-LD currency codes that count as RUB.
_RUB_CURRENCIES: frozenset[str] = frozenset({"RUB", "RUR", "643", "₽"})

# Curated pool of proven RU shops. Their URLs from SearXNG get bumped to
# the front of the fetch queue, so even when SearXNG returns a mix of
# blogs / forums / unknown shops, the actual product cards are tried
# first. Order = priority (higher = first).
_SHOP_POOL: tuple[str, ...] = (
    # Apple / electronics specialists — clean per-product JSON-LD
    "re-store.ru", "biggeek.ru", "cmstore.ru", "pitergsm.ru", "gbstore.ru",
    "i-point.ru", "doctorhead.ru",
    # Big-box electronics
    "mvideo.ru", "eldorado.ru", "technopark.ru", "dns-shop.ru",
    "citilink.ru", "holodilnik.ru", "onlinetrade.ru", "ситилинк.рф",
    # Office / printers / cartridges
    "foroffice.ru", "komus.ru", "officemag.ru", "sima-land.ru",
    # Tires
    "koleso.ru", "kolesa-darom.ru", "shinservice.ru", "4tochki.ru",
    "rossko.ru", "shinatorg.ru",
    # Apparel
    "poizonshop.ru", "kupivip.ru", "respect-shoes.ru", "sneakerhead.ru",
    "rendez-vous.ru", "spasibo.ru",
    # Generic price aggregators with reliable JSON-LD per offer
    "e-katalog.ru", "n-katalog.ru", "price.ru",
)

# Provider-level discovery seeds. These are domains/sitemaps, not product or
# query hardcodes. They cover the jury categories and let the Runet source keep
# working when public SearXNG upstreams are blocked.
_PROVIDER_SITEMAPS: tuple[str, ...] = (
    # Tyres
    "https://koleso.ru/sitemap.xml",
    "https://www.kolesa-darom.ru/sitemapxml/moskva/sitemap_index.xml",
    "https://www.4tochki.ru/external_upload/sitemaps/www.4tochki.ru/sitemap-index.xml",
    "https://www.shinservice.ru/sitemap.xml",
    # Office / printers / cartridges / paper
    "https://www.kns.ru/sitemap.xml",
    "https://cartridge.ru/sitemap.xml",
    "https://global-cartridge.ru/sitemap_200_5573.xml",
    "https://www.officemag.ru/sitemap/sitemap.xml",
    "https://komus.com/sitemap.xml",
    "https://www.onliner.by/sitemap.xml",
    "https://foroffice.ru/sitemap.xml",
    # Apparel / shoes
    "https://groupprice.ru/sitemap.xml",
    "https://respect-shoes.ru/sitemap.xml",
    "https://street-beat.ru/sitemap/sitemap.xml",
    "https://sneakerhead.ru/sitemap.xml",
    "https://www.rendez-vous.ru/sitemap.xml",
    # Electronics
    "https://doctorhead.ru/sitemap/sitemap-standart.xml",
    "https://cmstore.ru/sitemap.xml",
    "https://pitergsm.ru/sitemap.xml",
    "https://www.technopark.ru/sitemap.xml",
)

_SITEMAP_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)
_PRODUCT_URL_MARKERS = re.compile(
    r"/(?:product|products|catalog|katalog|tovar|item|goods|shop|p|card|shiny|tyres|tires|bumaga|paper|kartridj|kartridzh|printer)/",
    re.IGNORECASE,
)
_BAD_URL_MARKERS = re.compile(
    r"/(?:search|cart|basket|compare|favorite|favorites|login|register|blog|news|article|brand|brands)(?:/|$)",
    re.IGNORECASE,
)
_PRICE_TEXT_RE = re.compile(r"(?:от\s*)?(\d[\d\s\xa0]{2,})\s*(?:₽|руб\.?|р\.)", re.IGNORECASE)
_META_RE_TEMPLATE = r'<meta[^>]+(?:property|name|itemprop)=["\']{key}["\'][^>]+content=["\']([^"\']+)["\']'
_META_RE_TEMPLATE_REV = r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name|itemprop)=["\']{key}["\']'
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL,
)
_DIGITAL_DATA_RE = re.compile(
    r'window\.digitalData\s*=\s*(\{.*?\})\s*;', re.IGNORECASE | re.DOTALL,
)
_NUXT_DATA_RE = re.compile(
    r'window\.__(?:NUXT|PRELOADED_STATE|INITIAL_STATE)__\s*=\s*(\{.*?\})\s*;', re.IGNORECASE | re.DOTALL,
)
_MICRODATA_SCOPE_RE = re.compile(
    r'<[^>]+itemscope[^>]+itemtype=["\'][^"\']*schema\.org/Product["\'][^>]*>',
    re.IGNORECASE,
)
_MICRODATA_PROP_RE = re.compile(
    r'<[^>]+itemprop=["\'](\w+)["\'][^>]*(?:content=["\']([^"\']*)["\']|>([^<]*)(?=<))',
    re.IGNORECASE,
)

# Plausible RU consumer-good price range. Anything outside is almost
# certainly a categorical aggregate ("starting from 100 ₽") or a typo
# ("9 999 999 ₽" for a printer). Keeps mins/avgs sane.
_MIN_PRICE = Decimal(150)
_MAX_PRICE = Decimal(2_000_000)

# Tokens excluded from the "name must contain a query token" relevance
# check. Russian + English low-content words.
_STOPWORDS: frozenset[str] = frozenset({
    "и", "в", "на", "с", "от", "до", "по", "для", "the", "a", "an",
    "купить", "цена", "оригинал", "оригинальный", "новый", "руб", "рублей",
    "москва", "санкт", "петербург", "спб",
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


def _is_russian_tld(url: str) -> bool:
    """Reject obvious non-RU shops (currys.co.uk, jabko.ua, ...) up-front.

    Foreign sites usually price in a non-RUB currency that we'd silently
    relabel as RUB, polluting min/avg/median in the Best-Deal block.
    """
    host = urlparse(url).netloc.lower()
    if not host:
        return False
    return any(host.endswith(tld) for tld in _ALLOWED_TLDS)


def _walk_jsonld(html: str) -> list[dict[str, Any]]:
    """Yield every JSON-LD payload that could plausibly *be* a single
    product card.

    We expand ``@graph`` and ``mainEntity`` (those wrap a single product
    in a top-level container), but **deliberately do not expand
    ``itemListElement``** — that's an ItemList enumerating the children
    of a category page, and pulling its first child gave us bugs like
    "HP printer for 9 266 ₽" when the actual page was a printers
    catalogue, not a product card.
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
        me = node.get("mainEntity")
        if isinstance(me, (dict, list)):
            stack.append(me)
    return out


def _is_product(payload: dict[str, Any]) -> bool:
    type_ = payload.get("@type")
    if isinstance(type_, list):
        return any(t == "Product" for t in type_)
    return type_ == "Product"


def _is_category_listing(payload: dict[str, Any]) -> bool:
    """True if the JSON-LD block is an explicit category container.

    We *don't* treat `Product` + `AggregateOffer` as a category: shops
    like campioshop.ru use that shape to encode size/colour variants of
    a single t-shirt. The price extraction layer below mines the nested
    `offers.offers[]` when the top-level price is absent, so a real
    multi-variant product still produces an offer.
    """
    t = payload.get("@type")
    types = t if isinstance(t, list) else [t]
    listing = {"ItemList", "CollectionPage", "OfferCatalog", "BreadcrumbList"}
    return any(x in listing for x in types)


def _currency_ok(raw: Any) -> bool:
    """True if the offer currency is RUB / RUR / 643 / ₽, or absent.

    Absent currency is permitted because many small RU shops omit the
    `priceCurrency` field; the TLD whitelist (`_is_russian_tld`) makes
    that assumption safe.
    """
    if raw is None:
        return True
    s = str(raw).strip().upper()
    if not s:
        return True
    return s in _RUB_CURRENCIES


def _parse_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(raw).replace(",", "."))
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except (ValueError, ArithmeticError):
        return None


def _parse_price_text(text: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d]", "", text)
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except ArithmeticError:
        return None
    return value if _MIN_PRICE <= value <= _MAX_PRICE else None


def _walk_offer_prices(node: Any) -> list[Decimal]:
    """Collect every concrete RUB ``price`` value reachable from a
    Schema.org Offer / AggregateOffer node.

    Two real-world shapes drove this:

    * Plain ``Offer`` with ``price``.
    * ``AggregateOffer`` with no top-level price, ``offers[]`` of
      per-variant ``Offer`` objects (size/colour SKUs) each with their
      own ``price`` — as on campioshop.ru, kupivip.ru, etc.

    ``lowPrice`` / ``highPrice`` are deliberately *not* picked up here:
    they're shop-wide range markers that historically poisoned the
    "printer for 1M ₽" case. The min-of-children rule below gives us a
    safe equivalent of `lowPrice` for genuine multi-variant products
    without admitting category-wide ranges.
    """
    if node is None:
        return []
    if isinstance(node, list):
        out: list[Decimal] = []
        for n in node:
            out.extend(_walk_offer_prices(n))
        return out
    if not isinstance(node, dict):
        return []
    if not _currency_ok(node.get("priceCurrency")):
        return []
    prices: list[Decimal] = []
    direct = _parse_decimal(node.get("price"))
    if direct is not None:
        prices.append(direct)
    nested = node.get("offers")
    if isinstance(nested, (list, dict)):
        prices.extend(_walk_offer_prices(nested))
    return prices


def _price_from(payload: dict[str, Any]) -> Decimal | None:
    """Pull a single, concrete RUB price out of a Schema.org Product block.

    Strategy: collect every concrete ``price`` value reachable through
    ``Offer`` / ``AggregateOffer.offers[]`` nesting, then return the
    minimum (matches "from X ₽" semantics shops use when a product has
    multiple variants). ``lowPrice`` / ``highPrice`` are never used —
    those are category-wide ranges, not per-variant prices.
    """
    candidates: list[Decimal] = []
    candidates.extend(_walk_offer_prices(payload.get("offers")))
    if _currency_ok(payload.get("priceCurrency")):
        top = _parse_decimal(payload.get("price"))
        if top is not None:
            candidates.append(top)
    valid = [p for p in candidates if _MIN_PRICE <= p <= _MAX_PRICE]
    return min(valid) if valid else None


def _image_from(payload: dict[str, Any]) -> str | None:
    """Pick the first usable image URL from a Schema.org Product.

    Handles every shape we've seen: bare string, list of strings, list of
    objects, `ImageObject` with `url` / `contentUrl` / `@id` (the last
    one is the JSON-LD "node identifier", which on most shops is the
    real image URL).
    """
    def normalise(value: Any) -> str | None:
        if isinstance(value, str):
            v = value.strip()
            if v.startswith("//"):
                v = "https:" + v
            return v if v.startswith(("http://", "https://")) else None
        if isinstance(value, dict):
            for key in ("contentUrl", "url", "@id"):
                got = normalise(value.get(key))
                if got is not None:
                    return got
        return None

    image = payload.get("image")
    if isinstance(image, list):
        for item in image:
            got = normalise(item)
            if got is not None:
                return got
        return None
    return normalise(image)


def _brand_from(payload: dict[str, Any]) -> str:
    brand = payload.get("brand")
    if isinstance(brand, dict):
        return str(brand.get("name") or "")
    if isinstance(brand, str):
        return brand
    return ""


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "value", "text", "description"):
            text = _extract_text(value.get(key))
            if text:
                return text
    return ""


def _characteristics_from(payload: dict[str, Any], url: str) -> dict[str, str]:
    chars = {
        "site": urlparse(url).netloc,
        "brand": _brand_from(payload),
    }
    for key in ("sku", "model", "mpn", "description"):
        text = _extract_text(payload.get(key))
        if text:
            chars[key] = text[:500]

    offers = payload.get("offers")
    if isinstance(offers, dict):
        availability = _extract_text(offers.get("availability"))
        if availability:
            chars["availability"] = availability.rsplit("/", 1)[-1]
        seller = offers.get("seller")
        seller_name = _extract_text(seller)
        if seller_name:
            chars["seller"] = seller_name

    props = payload.get("additionalProperty") or payload.get("additionalProperties")
    if isinstance(props, dict):
        props = [props]
    if isinstance(props, list):
        for item in props:
            if not isinstance(item, dict):
                continue
            name = _extract_text(item.get("name") or item.get("propertyID"))
            value = _extract_text(item.get("value") or item.get("description"))
            if name and value:
                chars[name[:80]] = value[:300]
    return {k: v for k, v in chars.items() if v}


_TOKEN_RE = re.compile(r"[\w\d]+", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """Lower-case alpha-numeric tokens with stopwords stripped.

    Length floor is 2 (not 3) so model designators like ``s24`` / ``m3``
    / ``15`` survive — those are usually the only distinguishing token
    for a relevance match.
    """
    return {
        t for t in (m.group().lower() for m in _TOKEN_RE.finditer(text))
        if len(t) >= 2 and t not in _STOPWORDS
    }


def _name_matches_query(name: str, query_tokens: set[str]) -> bool:
    """The product name must overlap the search query by at least one
    meaningful token. Otherwise we're picking up a wholly unrelated card
    that happened to be linked from the page (e.g. "Recommended for you"
    blocks on category pages).
    """
    if not query_tokens:
        return True
    name_tokens = _tokenize(name)
    matched = name_tokens & query_tokens
    if not matched:
        return False
    # Model/size tokens are the discriminators: "iphone 15 128" must not
    # match iPhone 14/16 just because "iphone" and "128" overlap; tyre
    # sizes must preserve 205/55/r16 as well.
    strong_tokens = {t for t in query_tokens if any(ch.isdigit() for ch in t)}
    if strong_tokens:
        # Allow "128" to match "128gb", "r16" to match "r16c", etc. —
        # unit suffixes are stripped in the name but preserved in the query.
        def _strong_covered(tok: str) -> bool:
            return tok in name_tokens or any(nt.startswith(tok) for nt in name_tokens)
        if not all(_strong_covered(t) for t in strong_tokens):
            return False
    latin_tokens = {t for t in query_tokens if len(t) >= 3 and re.fullmatch(r"[a-z]+", t)}
    if latin_tokens and not latin_tokens <= name_tokens:
        return False
    overlap = len(matched) / len(query_tokens)
    # When all strong/numeric tokens are present, the product is a plausible
    # match even if Russian adjectives (зимние, шипованные) don't appear in
    # the product title. Lower the threshold so "шины R15 зимние" matches
    # "Nokian Nordman 185/65 R15 88T шипованная".
    threshold = 0.3 if strong_tokens else 0.5
    return overlap >= threshold


def _query_url_score(url: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    path = unquote(urlparse(url).path).replace("-", " ").replace("_", " ").lower()
    url_tokens = _tokenize(path)
    if not url_tokens:
        return 0.0
    strong_tokens = {t for t in query_tokens if any(ch.isdigit() for ch in t)}
    if strong_tokens and not strong_tokens <= url_tokens:
        return 0.0
    matched = query_tokens & url_tokens
    return len(matched) / len(query_tokens)


def _looks_like_product_url(url: str) -> bool:
    path = urlparse(url).path
    if not _PRODUCT_URL_MARKERS.search(path) or _BAD_URL_MARKERS.search(path):
        return False
    # Brand/category landing pages are useful for discovery, but should not be
    # treated as product URLs by the sitemap ranker. Product pages usually have
    # a slug with model/article details, not just `/catalog/tyres/brand/r15/`.
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) <= 4 and any(p.lower().startswith("r") and p[1:].isdigit() for p in parts):
        return False
    return True


def _looks_like_search_query_url(url: str) -> bool:
    """True only for in-shop *search-query* URLs (?q=… / ?text=… / etc.).

    Category-root URLs are kept on purpose: many shops only expose the
    JSON-LD product card via a category page that lists ``ItemList →
    itemListElement[].item.url``. The runet scraper mines those children
    in a second pass (see ``RunetScraper._fetch_with_listing_followup``)
    rather than ignoring the category outright.
    """
    qs = (urlparse(url).query or "").lower()
    return any(key + "=" in qs for key in ("q", "query", "search", "text", "find"))


def _extract_listing_child_urls(payloads: list[dict[str, Any]]) -> list[str]:
    """From a category page's JSON-LD, pull child-product URLs.

    Most shops (koleso.ru, foroffice.ru, sportmaster.ru, …) ship an
    ``ItemList`` whose ``itemListElement[].item.url`` points at the
    actual product card. We collect those URLs in listing order so the
    first few correspond to the shop's "top results" for the query.
    """
    out: list[str] = []
    for payload in payloads:
        ile = payload.get("itemListElement")
        if not isinstance(ile, list):
            continue
        for entry in ile:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item")
            url = None
            if isinstance(item, dict):
                url = item.get("url") or item.get("@id")
            elif isinstance(item, str):
                url = item
            else:
                url = entry.get("url") or entry.get("@id")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                out.append(url)
    # de-dup while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def _shop_priority(url: str) -> int:
    """Lower number = fetched earlier. Curated shops outrank unknowns."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for i, shop in enumerate(_SHOP_POOL):
        if host == shop or host.endswith("." + shop):
            return i
    return len(_SHOP_POOL) + 1


def _product_url_from(payload: dict[str, Any], fallback: str) -> str:
    raw = payload.get("url")
    if isinstance(raw, str) and raw.startswith(("http://", "https://")):
        return raw
    offers = payload.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers and isinstance(offers[0], dict) else None
    if isinstance(offers, dict):
        raw = offers.get("url")
        if isinstance(raw, str) and raw.startswith(("http://", "https://")):
            return raw
    return fallback


def _meta_content(html: str, key: str) -> str | None:
    escaped = re.escape(key)
    for template in (_META_RE_TEMPLATE, _META_RE_TEMPLATE_REV):
        match = re.search(template.format(key=escaped), html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return html_lib.unescape(match.group(1)).strip()
    return None


def _strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _html_title(html: str) -> str | None:
    for key in ("og:title", "twitter:title"):
        value = _meta_content(html, key)
        if value:
            return value
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        text = _strip_tags(match.group(1))
        if text:
            return text
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        text = _strip_tags(match.group(1))
        return re.split(r"\s+[|—-]\s+", text)[0].strip() or None
    return None


def _html_image(html: str, page_url: str) -> str | None:
    for key in ("og:image", "twitter:image", "image"):
        value = _meta_content(html, key)
        if value:
            return urljoin(page_url, value.strip())
    match = re.search(r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    if match:
        value = html_lib.unescape(match.group(1)).strip()
        if value and not value.startswith("data:"):
            return urljoin(page_url, value)
    return None


def _html_price(html: str) -> Decimal | None:
    meta_price = _meta_content(html, "product:price:amount") or _meta_content(html, "price")
    price = _parse_decimal(meta_price)
    if price is not None and _MIN_PRICE <= price <= _MAX_PRICE:
        return price
    visible = _strip_tags(html[:700_000])
    prices = [p for match in _PRICE_TEXT_RE.finditer(visible) if (p := _parse_price_text(match.group(1))) is not None]
    return min(prices) if prices else None


def _html_characteristics(html: str, page_url: str) -> dict[str, str]:
    chars: dict[str, str] = {"site": urlparse(page_url).netloc, "extraction_stage": "html"}
    description = _meta_content(html, "description") or _meta_content(html, "og:description")
    if description:
        chars["description"] = description[:500]
    for key, label in (("product:brand", "brand"), ("product:retailer_item_id", "sku")):
        value = _meta_content(html, key)
        if value:
            chars[label] = value[:200]

    # Generic table/dl specs. Good enough for KNS/Respect/Bitrix-style pages
    # without adding site-specific parsers.
    for left, right in re.findall(r"<tr[^>]*>\s*<t[hd][^>]*>(.*?)</t[hd]>\s*<td[^>]*>(.*?)</td>", html, re.I | re.S):
        name = _strip_tags(left)
        value = _strip_tags(right)
        if name and value and len(name) <= 80:
            chars.setdefault(name, value[:300])
        if len(chars) >= 30:
            break
    if len(chars) < 8:
        for left, right in re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", html, re.I | re.S):
            name = _strip_tags(left)
            value = _strip_tags(right)
            if name and value and len(name) <= 80:
                chars.setdefault(name, value[:300])
            if len(chars) >= 30:
                break
    return {k: v for k, v in chars.items() if v}


def _find_product_nodes(obj: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Recursively find objects that look like product nodes (name + price)."""
    if depth > 7:
        return []
    results: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        name_keys = {"name", "title", "productName", "product_name", "displayName"}
        price_keys = {"price", "currentPrice", "finalPrice", "salePrice", "basePrice",
                      "discountedPrice", "buyPrice", "amount"}
        if obj.keys() & name_keys and obj.keys() & price_keys:
            results.append(obj)
        else:
            for v in obj.values():
                results.extend(_find_product_nodes(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj[:20]:
            results.extend(_find_product_nodes(item, depth + 1))
    return results


def _node_to_offer(
    node: dict[str, Any],
    page_url: str,
    *,
    query_tokens: set[str],
    stage: str,
) -> ProductOffer | None:
    """Convert a generic product node (from __NEXT_DATA__ or digitalData) to ProductOffer."""
    name_keys = ("name", "title", "productName", "product_name", "displayName")
    price_keys = ("price", "currentPrice", "finalPrice", "salePrice", "basePrice",
                  "discountedPrice", "buyPrice", "amount")
    name = next((str(node[k]).strip() for k in name_keys if node.get(k)), None)
    if not name or not _name_matches_query(name, query_tokens):
        return None

    raw_price = next((node[k] for k in price_keys if k in node), None)
    price = _parse_decimal(raw_price)
    if price is None or not (_MIN_PRICE <= price <= _MAX_PRICE):
        return None

    image_raw = node.get("image") or node.get("picture") or node.get("photo") or node.get("img")
    if isinstance(image_raw, list):
        image_raw = image_raw[0] if image_raw else None
    if isinstance(image_raw, dict):
        image_raw = image_raw.get("src") or image_raw.get("url") or image_raw.get("original")
    image_url = urljoin(page_url, str(image_raw).strip()) if isinstance(image_raw, str) and image_raw else None

    url_raw = node.get("url") or node.get("link") or node.get("href")
    offer_url = urljoin(page_url, str(url_raw).strip()) if isinstance(url_raw, str) and url_raw else page_url

    chars: dict[str, str] = {
        "site": urlparse(page_url).netloc,
        "extraction_stage": stage,
    }
    for k in ("brand", "sku", "article", "model", "description"):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            chars[k] = v.strip()[:300]

    try:
        return ProductOffer(
            source=SourceKind.RUNET,
            name=name,
            price=price,
            currency="RUB",
            url=offer_url,
            image=image_url,
            characteristics=chars,
            seller=urlparse(page_url).netloc,
            rating=None,
            fetched_at=datetime.now(tz=UTC),
            cached=False,
        )
    except Exception:
        return None


def _offers_from_next_data(
    html: str, page_url: str, *, query_tokens: set[str],
) -> list[ProductOffer]:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = orjson.loads(match.group(1))
    except Exception:
        return []
    nodes = _find_product_nodes(data)
    offers: list[ProductOffer] = []
    seen: set[str] = set()
    for node in nodes:
        offer = _node_to_offer(node, page_url, query_tokens=query_tokens, stage="next_data")
        if offer is None:
            continue
        key = str(offer.url)
        if key in seen:
            continue
        seen.add(key)
        offers.append(offer)
        if len(offers) >= 5:
            break
    return offers


def _offers_from_digital_data(
    html: str, page_url: str, *, query_tokens: set[str],
) -> list[ProductOffer]:
    for pattern in (_DIGITAL_DATA_RE, _NUXT_DATA_RE):
        match = pattern.search(html[:2_000_000])
        if not match:
            continue
        try:
            data = orjson.loads(match.group(1))
        except Exception:  # noqa: S112
            continue
        nodes = _find_product_nodes(data)
        offers: list[ProductOffer] = []
        seen: set[str] = set()
        for node in nodes:
            offer = _node_to_offer(node, page_url, query_tokens=query_tokens, stage="digital_data")
            if offer is None:
                continue
            key = str(offer.url)
            if key in seen:
                continue
            seen.add(key)
            offers.append(offer)
            if len(offers) >= 5:
                break
        if offers:
            return offers
    return []


def _offers_from_microdata(
    html: str, page_url: str, *, query_tokens: set[str],
) -> list[ProductOffer]:
    if not _MICRODATA_SCOPE_RE.search(html):
        return []
    props: dict[str, str] = {}
    for m in _MICRODATA_PROP_RE.finditer(html[:1_000_000]):
        prop = m.group(1).lower()
        value = (m.group(2) or m.group(3) or "").strip()
        value = html_lib.unescape(value)
        if value and prop not in props:
            props[prop] = value[:300]

    name = props.get("name")
    if not name or not _name_matches_query(name, query_tokens):
        return []
    price = _parse_decimal(props.get("price")) or _parse_decimal(props.get("lowprice"))
    if price is None or not (_MIN_PRICE <= price <= _MAX_PRICE):
        return []
    image_raw = props.get("image")
    image_url = urljoin(page_url, image_raw) if image_raw else None
    chars: dict[str, str] = {
        "site": urlparse(page_url).netloc,
        "extraction_stage": "microdata",
    }
    for k in ("brand", "sku", "description", "model"):
        if k in props:
            chars[k] = props[k]
    try:
        offer = ProductOffer(
            source=SourceKind.RUNET,
            name=name,
            price=price,
            currency="RUB",
            url=page_url,
            image=image_url,
            characteristics=chars,
            seller=urlparse(page_url).netloc,
            rating=None,
            fetched_at=datetime.now(tz=UTC),
            cached=False,
        )
    except Exception:
        return []
    return [offer]


def _html_offer(page_url: str, html: str, *, query_tokens: set[str]) -> ProductOffer | None:
    name = _html_title(html)
    if not name or not _name_matches_query(name, query_tokens):
        return None
    price = _html_price(html)
    if price is None:
        return None
    image = _html_image(html, page_url)
    return ProductOffer(
        source=SourceKind.RUNET,
        name=name,
        price=price,
        currency="RUB",
        url=page_url,
        image=image,
        characteristics=_html_characteristics(html, page_url),
        seller=urlparse(page_url).netloc,
        rating=None,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


def _attribute_checked(offer: ProductOffer, query_attrs: ProductAttributes) -> ProductOffer | None:
    offer_attrs = merge_attributes(offer.attributes, extract_offer_attributes(offer))
    if query_attrs.confidence >= 0.3 and is_attribute_conflict(query_attrs, offer_attrs)[0]:
        return None
    return offer.model_copy(update={"attributes": offer_attrs})


def _to_offer(
    url: str,
    payload: dict[str, Any],
    *,
    query_tokens: set[str],
) -> ProductOffer | None:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    if not _name_matches_query(name, query_tokens):
        return None
    if _is_category_listing(payload):
        return None
    price = _price_from(payload)
    if price is None:
        return None
    return ProductOffer(
        source=SourceKind.RUNET,
        name=name,
        price=price,
        currency="RUB",
        url=url,
        image=_image_from(payload),
        characteristics=_characteristics_from(payload, url),
        seller=urlparse(url).netloc,
        rating=None,
        fetched_at=datetime.now(tz=UTC),
        cached=False,
    )


class RunetScraper:
    """4th source — SearXNG → top non-marketplace URLs → JSON-LD."""

    source: SourceKind = SourceKind.RUNET

    def __init__(self, timeout_s: float = 7.0, max_urls: int = 25) -> None:
        # `timeout_s` is per-request. Tight on purpose: big shops
        # (dns-shop, citilink, mvideo, sportmaster, …) tend to hang on
        # datacenter IPs entirely rather than serve a 403, so a long
        # timeout just delays the inevitable. With concurrency 12 + 7s
        # cap we burn through a dead shop and move on quickly.
        self._timeout = timeout_s
        self._max_urls = max_urls

    _FETCH_CONCURRENCY: int = 12

    async def _discover_from_sitemaps(self, q: str, query_tokens: set[str], cache: Any) -> list[str]:
        digest = hashlib.sha1(q.encode("utf-8"), usedforsecurity=False).hexdigest()
        cache_key = f"runet:sitemap:{digest}"
        cached = await cache.get(cache_key) if cache is not None else None
        if isinstance(cached, list):
            urls = [u for u in cached if isinstance(u, str)]
            if urls:
                log.debug("runet.sitemap_cache_hit", q=q, urls=len(urls))
                return urls[: self._max_urls]

        urls = await self._sitemap_corpus(cache)

        ranked: list[tuple[float, str]] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen or _is_excluded(url) or not _is_russian_tld(url):
                continue
            seen.add(url)
            score = _query_url_score(url, query_tokens)
            if score > 0:
                ranked.append((score, url))
        ranked.sort(key=lambda item: (-item[0], _shop_priority(item[1]), item[1]))
        result = [url for _, url in ranked[: self._max_urls]]
        if result and cache is not None:
            await cache.set(cache_key, result, ttl_seconds=6 * 3600)
        log.info("runet.sitemap_discovery", returned=len(result), corpus=len(urls))
        return result

    async def _sitemap_corpus(self, cache: Any) -> list[str]:
        cache_key = "runet:sitemap:corpus:v1"
        cached = await cache.get(cache_key) if cache is not None else None
        if isinstance(cached, list):
            urls = [u for u in cached if isinstance(u, str)]
            if urls:
                log.debug("runet.sitemap_corpus_cache_hit", urls=len(urls))
                return urls

        urls: list[str] = []
        child_sitemaps: list[str] = []
        timeout = min(self._timeout, 6.0)
        async with httpx.AsyncClient(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
            for sitemap_url in _PROVIDER_SITEMAPS:
                try:
                    resp = await client.get(sitemap_url)
                    if resp.status_code != 200:
                        continue
                    body = resp.text[:800_000]
                except (httpx.HTTPError, ValueError) as exc:
                    log.debug("runet.sitemap_fetch_failed", url=sitemap_url, error=str(exc))
                    continue
                locs = [html_lib.unescape(m.group(1).strip()) for m in _SITEMAP_LOC_RE.finditer(body)]
                child_sitemaps.extend([u for u in locs if "sitemap" in u.lower()][:6])
                urls.extend(u for u in locs if _looks_like_product_url(u))

            for sitemap_url in child_sitemaps[:60]:
                try:
                    resp = await client.get(sitemap_url)
                    if resp.status_code != 200:
                        continue
                    body = resp.text[:1_200_000]
                except (httpx.HTTPError, ValueError) as exc:
                    log.debug("runet.sitemap_child_fetch_failed", url=sitemap_url, error=str(exc))
                    continue
                locs = [html_lib.unescape(m.group(1).strip()) for m in _SITEMAP_LOC_RE.finditer(body)]
                urls.extend(u for u in locs if _looks_like_product_url(u))

        result: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen or _is_excluded(url) or not _is_russian_tld(url):
                continue
            seen.add(url)
            result.append(url)
        if result and cache is not None:
            await cache.set(cache_key, result, ttl_seconds=12 * 3600)
        log.info("runet.sitemap_corpus_built", urls=len(result), raw=len(urls))
        return result

    async def _fetch_searxng(self, searxng_url: str, q: str) -> dict | None:
        """Query SearXNG with one retry when the result set is empty.

        SearXNG returns ``results: []`` plus an ``unresponsive_engines`` list
        when its upstream engines hit captcha/rate-limits — that's most of
        our instability. A short backoff between two attempts often lets
        at least one engine recover (timeout engines retry instantly;
        captcha-suspended ones still skip the second pass, but DDG/Google
        timeouts frequently succeed second time).
        """
        params = {
            "q": f"{q} купить цена",
            "format": "json",
            "language": "ru-RU",
            "categories": "general",
            "safesearch": 0,
        }
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(
                    headers=_HEADERS, timeout=self._timeout, follow_redirects=True,
                ) as client:
                    resp = await client.get(f"{searxng_url}/search", params=params)
                    resp.raise_for_status()
                    body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("runet.searxng_failed", attempt=attempt, error=str(exc))
                if attempt == 2:
                    return None
                await asyncio.sleep(1.0)
                continue
            results = body.get("results") or []
            if results:
                return body
            unresp = body.get("unresponsive_engines") or []
            log.info(
                "runet.searxng_empty", attempt=attempt, unresponsive=len(unresp),
            )
            if attempt == 1:
                await asyncio.sleep(1.2)
        return body

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
            cache = await get_search_cache()
            query_tokens = _tokenize(q)
            query_attrs = extract_query_attributes(q)

            # 1) URL discovery from provider sitemaps. This is the primary
            # on-prem path: no public search engine, no product hardcode.
            candidate_urls = await self._discover_from_sitemaps(q, query_tokens, cache)

            # 2) URL discovery via self-hosted SearXNG. Cached in Redis
            #    because SearXNG's upstream engines (Brave/DDG/Startpage) hit
            #    captcha + "Suspended: too many requests" within a handful
            #    of queries, and re-hitting them empty-handed only deepens
            #    the ban.
            digest = hashlib.sha1(q.encode("utf-8"), usedforsecurity=False).hexdigest()
            cache_key = f"runet:searxng:{digest}"
            cached_payload = await cache.get(cache_key) if cache is not None else None
            searxng_urls: list[str] = []
            if isinstance(cached_payload, list) and len(candidate_urls) < self._max_urls:
                searxng_urls = [u for u in cached_payload if isinstance(u, str)]
                log.debug("runet.searxng_cache_hit", q=q, urls=len(searxng_urls))

            if not searxng_urls and len(candidate_urls) < self._max_urls:
                sx_body = await self._fetch_searxng(searxng_url, q)
                if sx_body is None and not candidate_urls:
                    scrape_requests_total.labels(
                        source=self.source.value, outcome="blocked", proxy_tier="none",
                    ).inc()
                    return ScrapeResult(
                        source=self.source, offers=[], error="SearXNG unavailable",
                    )

                for r in (sx_body or {}).get("results") or []:
                    if not isinstance(r, dict):
                        continue
                    url = r.get("url")
                    if not isinstance(url, str) or _is_excluded(url):
                        continue
                    if not _is_russian_tld(url):
                        continue
                    if _looks_like_search_query_url(url):
                        # In-shop search-result URLs (?q=…) are dynamic
                        # listings — rarely worth fetching, and SearXNG
                        # returns plenty of static category pages anyway.
                        continue
                    searxng_urls.append(url)
                    if len(searxng_urls) >= self._max_urls:
                        break

                # Reorder so curated shops (`_SHOP_POOL`) get fetched first.
                searxng_urls.sort(key=_shop_priority)

                if searxng_urls and cache is not None:
                    # 6h TTL — SearXNG results shift slowly per query, but
                    # we don't want to pin them indefinitely either.
                    await cache.set(cache_key, searxng_urls, ttl_seconds=6 * 3600)

            if searxng_urls:
                seen_candidate_urls = set(candidate_urls)
                for url in searxng_urls:
                    if url not in seen_candidate_urls:
                        candidate_urls.append(url)
                        seen_candidate_urls.add(url)
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
            sem = asyncio.Semaphore(self._FETCH_CONCURRENCY)

            async with AsyncSession(impersonate="chrome", timeout=self._timeout) as session:
                async def fetch_page(url: str) -> str | None:
                    async with sem:
                        try:
                            page = await session.get(url, headers=_HEADERS)
                        except Exception as exc:
                            log.debug("runet.fetch_failed", url=url, error=str(exc))
                            return None
                    if page.status_code != 200:
                        return None
                    return page.text if isinstance(page.text, str) else None

                async def offers_from_page(url: str, *, cap: int = 5) -> list[ProductOffer]:
                    """Every relevant Product on the page, capped per parent URL."""
                    html = await fetch_page(url)
                    if html is None:
                        return []
                    page_offers: list[ProductOffer] = []
                    seen_page_urls: set[str] = set()
                    for payload in _walk_jsonld(html):
                        if not _is_product(payload):
                            continue
                        offer_url = _product_url_from(payload, url)
                        offer = _to_offer(offer_url, payload, query_tokens=query_tokens)
                        if offer is not None:
                            offer = _attribute_checked(offer, query_attrs)
                        if offer is not None:
                            key = str(offer.url)
                            if key in seen_page_urls:
                                continue
                            seen_page_urls.add(key)
                            page_offers.append(offer)
                            if len(page_offers) >= cap:
                                break
                    return page_offers

                async def fetch_one(url: str) -> list[ProductOffer]:
                    """Two-pass: direct Products → else mine ItemList children.

                    Most general-purpose searches (`шины nokian R16`,
                    `футболка adidas`) land us on a category page, not a
                    card. The category JSON-LD usually contains an
                    ItemList whose children point at real product cards,
                    so on a miss we follow up to 3 of them in parallel —
                    that's what turns "0 offers" into a real result for
                    those queries.
                    """
                    html = await fetch_page(url)
                    if html is None:
                        return []
                    payloads = _walk_jsonld(html)
                    page_offers: list[ProductOffer] = []
                    seen_page_urls: set[str] = set()
                    for payload in payloads:
                        if _is_product(payload):
                            offer_url = _product_url_from(payload, url)
                            offer = _to_offer(
                                offer_url, payload, query_tokens=query_tokens,
                            )
                            if offer is not None:
                                offer = _attribute_checked(offer, query_attrs)
                            if offer is not None:
                                key = str(offer.url)
                                if key in seen_page_urls:
                                    continue
                                seen_page_urls.add(key)
                                page_offers.append(offer)
                                if len(page_offers) >= 5:
                                    break
                    if page_offers:
                        return page_offers

                    # Stage 2: embedded JS state (__NEXT_DATA__, digitalData, Nuxt).
                    for embedded_offers in (
                        _offers_from_next_data(html, url, query_tokens=query_tokens),
                        _offers_from_digital_data(html, url, query_tokens=query_tokens),
                        _offers_from_microdata(html, url, query_tokens=query_tokens),
                    ):
                        checked = []
                        for o in embedded_offers:
                            o = _attribute_checked(o, query_attrs)
                            if o is not None:
                                checked.append(o)
                        if checked:
                            return checked

                    # Stage 3: generic meta + HTML heuristics.
                    html_offer = _html_offer(url, html, query_tokens=query_tokens)
                    if html_offer is not None:
                        html_offer = _attribute_checked(html_offer, query_attrs)
                    if html_offer is not None:
                        return [html_offer]

                    # Listing follow-up. Cap fan-out — a category can list
                    # 50+ items and we only need the first few good matches per parent.
                    child_urls = _extract_listing_child_urls(payloads)
                    if not child_urls:
                        return []
                    host = urlparse(url).netloc.lower()
                    child_urls = [
                        c for c in child_urls
                        if urlparse(c).netloc.lower() == host
                    ][:5]
                    if not child_urls:
                        return []
                    log.debug(
                        "runet.listing_followup", parent=url, children=len(child_urls),
                    )
                    child_tasks = [
                        asyncio.create_task(offers_from_page(c, cap=1)) for c in child_urls
                    ]
                    child_offers: list[ProductOffer] = []
                    try:
                        for coro in asyncio.as_completed(child_tasks):
                            child_offers.extend(await coro)
                            if len(child_offers) >= 5:
                                break
                    finally:
                        for t in child_tasks:
                            if not t.done():
                                t.cancel()
                    return child_offers[:5]

                tasks = [asyncio.create_task(fetch_one(u)) for u in candidate_urls]
                seen_urls: set[str] = set()
                try:
                    for coro in asyncio.as_completed(tasks):
                        for offer in await coro:
                            # Two different category pages often funnel into
                            # the same product card via ItemList children;
                            # drop the duplicate so the Best-Deal block isn't
                            # picking the same offer twice.
                            offer_url = str(offer.url)
                            if offer_url in seen_urls:
                                continue
                            seen_urls.add(offer_url)
                            offers.append(offer)
                            if on_offer is not None:
                                await on_offer(offer)
                            if len(offers) >= limit:
                                break
                        if len(offers) >= limit:
                            break
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()

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
