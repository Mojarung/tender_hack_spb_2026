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
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
import orjson
import structlog

from pricepulse.api.cache import get_search_cache
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
    return bool(name_tokens & query_tokens)


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

    def __init__(self, timeout_s: float = 7.0, max_urls: int = 25) -> None:
        # `timeout_s` is per-request. Tight on purpose: big shops
        # (dns-shop, citilink, mvideo, sportmaster, …) tend to hang on
        # datacenter IPs entirely rather than serve a 403, so a long
        # timeout just delays the inevitable. With concurrency 12 + 7s
        # cap we burn through a dead shop and move on quickly.
        self._timeout = timeout_s
        self._max_urls = max_urls

    _FETCH_CONCURRENCY: int = 12

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
            # 1) URL discovery via self-hosted SearXNG. Cached in Redis
            #    because SearXNG's upstream engines (Brave/DDG/Startpage) hit
            #    captcha + "Suspended: too many requests" within a handful
            #    of queries, and re-hitting them empty-handed only deepens
            #    the ban.
            digest = hashlib.sha1(q.encode("utf-8"), usedforsecurity=False).hexdigest()
            cache_key = f"runet:searxng:{digest}"
            cache = await get_search_cache()
            candidate_urls: list[str] = []
            cached_payload = await cache.get(cache_key) if cache is not None else None
            if isinstance(cached_payload, list):
                candidate_urls = [u for u in cached_payload if isinstance(u, str)]
                log.debug("runet.searxng_cache_hit", q=q, urls=len(candidate_urls))

            if not candidate_urls:
                sx_body = await self._fetch_searxng(searxng_url, q)
                if sx_body is None:
                    scrape_requests_total.labels(
                        source=self.source.value, outcome="blocked", proxy_tier="none",
                    ).inc()
                    return ScrapeResult(
                        source=self.source, offers=[], error="SearXNG unavailable",
                    )

                for r in sx_body.get("results") or []:
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
                    candidate_urls.append(url)
                    if len(candidate_urls) >= self._max_urls:
                        break

                # Reorder so curated shops (`_SHOP_POOL`) get fetched first.
                candidate_urls.sort(key=_shop_priority)

                if candidate_urls and cache is not None:
                    # 6h TTL — SearXNG results shift slowly per query, but
                    # we don't want to pin them indefinitely either.
                    await cache.set(cache_key, candidate_urls, ttl_seconds=6 * 3600)

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
            query_tokens = _tokenize(q)

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

                async def offer_from_page(url: str) -> ProductOffer | None:
                    """Top-level Product on the page → offer. None otherwise."""
                    html = await fetch_page(url)
                    if html is None:
                        return None
                    for payload in _walk_jsonld(html):
                        if not _is_product(payload):
                            continue
                        offer = _to_offer(url, payload, query_tokens=query_tokens)
                        if offer is not None:
                            return offer
                    return None

                async def fetch_one(url: str) -> ProductOffer | None:
                    """Two-pass: direct Product → else mine ItemList children.

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
                        return None
                    payloads = _walk_jsonld(html)
                    for payload in payloads:
                        if _is_product(payload):
                            offer = _to_offer(
                                url, payload, query_tokens=query_tokens,
                            )
                            if offer is not None:
                                return offer

                    # Listing follow-up. Cap fan-out — a category can list
                    # 50+ items and we only need one good match per parent.
                    child_urls = _extract_listing_child_urls(payloads)
                    if not child_urls:
                        return None
                    host = urlparse(url).netloc.lower()
                    child_urls = [
                        c for c in child_urls
                        if urlparse(c).netloc.lower() == host
                    ][:5]
                    if not child_urls:
                        return None
                    log.debug(
                        "runet.listing_followup", parent=url, children=len(child_urls),
                    )
                    child_tasks = [
                        asyncio.create_task(offer_from_page(c)) for c in child_urls
                    ]
                    try:
                        for coro in asyncio.as_completed(child_tasks):
                            offer = await coro
                            if offer is not None:
                                return offer
                    finally:
                        for t in child_tasks:
                            if not t.done():
                                t.cancel()
                    return None

                tasks = [asyncio.create_task(fetch_one(u)) for u in candidate_urls]
                seen_urls: set[str] = set()
                try:
                    for coro in asyncio.as_completed(tasks):
                        offer = await coro
                        if offer is None:
                            continue
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
