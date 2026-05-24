"""Ozon adapter — cookie-warmed L1 with PDP enrichment.

Strategy (May-2026 rewrite, validated end-to-end in ``ozon_research/``)
---------------------------------------------------------------------

Old approach (pure curl_cffi mobile-API impersonation) decayed against
Ozon's WAF — IP-only HTTP gets rejected on most networks now. New
approach is two-tier:

1.  :class:`OzonCookieWarmer` lazily drives nodriver against
    ``ozon.ru`` once per TTL to plant the session cookies
    (``abt_data``, ``__Secure-ext_xcid``, …). Ozon's anti-bot scores
    these as "human passed the challenge"; nodriver in 2026 auto-passes
    the challenge from a clean profile.
2.  Every request after warm-up uses ``curl_cffi`` with those cookies
    on the **desktop** ``composer-api`` host (``www.ozon.ru/api/...``).
    The mobile path is no longer used — desktop with warmed cookies is
    both more permissive and richer (full ``webProductHeading`` /
    ``webShortCharacteristics`` widgets).

On HTTP 403 from L1 we invalidate the cookie cache and re-warm once,
then retry. If the second attempt still fails, we fall back to
``fetch_ozon_via_browser`` — a same-origin fetch from inside the
warmed browser. After two consecutive L1 failures the cascade router
escalates this source for the next minute.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlparse

import orjson
import structlog

from pricepulse.antibot.browser_fetch import (
    backfill_from_pdp,
    chars_via_structural,
    extract_reviews,
    fetch_ozon_via_browser,
    get_ozon_cookie_warmer,
)
from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.observability.metrics import (
    scrape_duration_seconds,
    scrape_offers_returned_total,
    scrape_requests_total,
)
from pricepulse.scrapers.base import OnOffer, ScrapeResult

log = structlog.get_logger(__name__)

_BASE = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
_ENTRYPOINT_BASE = "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"

# Match the warmed-browser session: dweb_client UA family with browser-y
# accept-headers. Critical that this stays consistent with what
# OzonCookieWarmer's tab actually sends — otherwise WAF flags the mismatch.
_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "ru,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.ozon.ru/",
    "x-o3-app-name": "dweb_client",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# Reviews per offer — bumped from 3 to 10 so the modal carousel has
# a meaningful sample. Sorted newest-first via the API param below.
_REVIEWS_PER_OFFER = 10
# Chars per offer — bumped from 30 to 120 since the full Ozon spec
# page has 40-80 attributes for laptops/electronics and the user
# wants the modal to show the FULL list with its own scrollbar.
_CHARS_PER_OFFER = 120

# Full-characteristics layout containers, tried in order. The widget
# walker is forgiving — anything returned with chars merges into the
# short-list (deduped on name). When we hit the actual full-spec
# container Ozon returns ~80 attribute rows in one response.
_FULL_CHARS_CONTAINERS = (
    "characteristicsList",
    "webProductCharacteristics",
    "webShortCharacteristics",
    "pdpAtomicCharacteristics",
)

# Reviews API params — `sort=published_at_desc` returns the freshest
# reviews first (Ozon's own default for the page when you click
# "Сначала новые"). `limit=20` over-fetches so the dedupe + min-30-char
# filter still leaves us with ≥10 quality entries.
_REVIEWS_SORT = "published_at_desc"
_REVIEWS_PAGE_LIMIT = 20


# ---------------------------------------------------------------------------
# Pure parsing — search response → list of "stub" offer dicts
# ---------------------------------------------------------------------------
def _iter_search_widgets(layout_widgets: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, value in layout_widgets.items():
        if not isinstance(value, str):
            continue
        if not key.startswith(("searchResultsV2", "tileGridDesktop", "skuList")):
            continue
        try:
            out.append(orjson.loads(value))
        except orjson.JSONDecodeError:
            continue
    return out


def _stub_offers_from_search(payloads: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Pull URL + whatever stub fields the search row carried. Empty
    name/price/sku are expected — backfill_from_pdp fills them."""
    stubs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        for item in payload.get("items") or []:
            tracking = (item.get("cellTrackingInfo") or {}).get("product") or {}
            sku = str(tracking.get("id") or item.get("itemId") or "")
            if sku and sku in seen:
                continue
            link = (
                tracking.get("link")
                or (item.get("action") or {}).get("link")
                or item.get("link")
            )
            if not link:
                continue
            if link.startswith("/"):
                link = f"https://www.ozon.ru{link}"
            stubs.append({
                "sku": sku,
                "name": tracking.get("title"),
                "price": tracking.get("finalPrice") or tracking.get("price"),
                "url": link,
                "image": tracking.get("imageUrl") or tracking.get("image"),
                "rating": tracking.get("rating"),
                "reviews_count": tracking.get("reviewsCount"),
                "seller": tracking.get("sellerName"),
            })
            if sku:
                seen.add(sku)
            if len(stubs) >= limit:
                return stubs
    return stubs


def _price_to_decimal(raw: Any) -> Decimal | None:
    """`38 112 ₽` / `38112` / Decimal → Decimal | None."""
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    if isinstance(raw, str):
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return None
        try:
            return Decimal(digits)
        except ArithmeticError:
            return None
    return None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# HTTP helpers — curl_cffi with warmed cookies
# ---------------------------------------------------------------------------
def _apply_cookies(session: Any, cookies: list[dict[str, Any]]) -> None:
    for c in cookies:
        name, value = c.get("name"), c.get("value")
        if not name or value is None:
            continue
        try:
            session.cookies.set(
                name, value,
                domain=c.get("domain") or ".ozon.ru",
                path=c.get("path") or "/",
            )
        except Exception as exc:    # invalid cookie entries are best-effort skipped
            log.debug("ozon.l1.skip_cookie", name=name, error=str(exc))


async def _get_json(session: Any, sub_path: str, *, base: str = _BASE) -> tuple[dict | None, int]:
    """Returns (parsed_body_or_none, http_status)."""
    url = f"{base}?url={quote(sub_path, safe='')}"
    try:
        resp = await session.get(url, headers=_HEADERS)
    except Exception as exc:
        log.warning("ozon.l1.network_error", error=str(exc), path=sub_path)
        return None, 0
    if resp.status_code != 200:
        return None, resp.status_code
    try:
        return orjson.loads(resp.content), 200
    except orjson.JSONDecodeError:
        return None, 200


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------
class OzonScraper:
    source: SourceKind = SourceKind.OZON

    def __init__(
        self,
        timeout_s: float = 8.0,
        *,
        enable_l2: bool = True,
        reviews_per_offer: int = _REVIEWS_PER_OFFER,
        chars_per_offer: int = _CHARS_PER_OFFER,
    ) -> None:
        self._timeout = timeout_s
        self._enable_l2 = enable_l2
        self._reviews_per_offer = reviews_per_offer
        self._chars_per_offer = chars_per_offer

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        """One-shot search → up to `limit` enriched offers (chars + reviews)."""
        # 1. Get cookies (warms on first call, cached otherwise).
        try:
            warmer = await get_ozon_cookie_warmer()
            cookies = await warmer.get_cookies()
        except Exception as exc:
            log.warning("ozon.cookies.warmup_failed", error=str(exc))
            return ScrapeResult(
                source=self.source, offers=[],
                error=f"cookie warm-up failed: {exc}",
            )
        if not cookies:
            return ScrapeResult(
                source=self.source, offers=[],
                error="cookie warm-up returned no cookies",
            )

        # 2. L1 search → stubs. One retry with refreshed cookies on 403.
        with scrape_duration_seconds.labels(source=self.source.value).time():
            stubs = await self._search_l1(query, limit, cookies)
            if stubs is None:    # 403 — try again with fresh cookies
                log.info("ozon.l1.refreshing_cookies")
                await warmer.invalidate()
                cookies = await warmer.get_cookies(force=True)
                stubs = await self._search_l1(query, limit, cookies)

            if stubs is None and self._enable_l2:
                # 3. L2 fallback — same-origin browser fetch.
                log.info("ozon.escalate_l2")
                stubs = await self._search_l2(query, limit)

            if stubs is None:
                scrape_requests_total.labels(
                    source=self.source.value, outcome="blocked", proxy_tier="none",
                ).inc()
                return ScrapeResult(
                    source=self.source, offers=[],
                    error="ozon search blocked at L1 and L2",
                )
            if not stubs:
                scrape_requests_total.labels(
                    source=self.source.value, outcome="ok", proxy_tier="none",
                ).inc()
                return ScrapeResult(source=self.source, offers=[])

            # 4. Enrich each stub from its PDP — chars + reviews + backfill.
            #    Parallel — small fan-out keeps total time ~= one PDP fetch.
            enriched = await self._enrich_all(stubs, cookies)

        # 5. Convert to ProductOffer; drop ones still missing essentials.
        out: list[ProductOffer] = []
        now = datetime.now(tz=UTC)
        for stub in enriched:
            offer = self._to_product_offer(stub, now)
            if offer is None:
                continue
            out.append(offer)
            if on_offer is not None:
                await on_offer(offer)

        scrape_requests_total.labels(
            source=self.source.value, outcome="ok", proxy_tier="none",
        ).inc()
        scrape_offers_returned_total.labels(source=self.source.value).inc(len(out))
        log.info("ozon.ok", returned=len(out), requested=limit)
        return ScrapeResult(source=self.source, offers=out)

    # -- L1 path --------------------------------------------------------------
    async def _search_l1(
        self,
        query: NormalizedQuery,
        limit: int,
        cookies: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Returns the stub list, or ``None`` on HTTP error (caller may
        retry with fresh cookies / escalate)."""
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:    # pragma: no cover
            log.warning("ozon.curl_cffi_missing")
            return None

        text = query.normalized or query.raw
        sub = f"/search/?text={quote(text)}&from_global=true"

        async with AsyncSession(impersonate="chrome", timeout=self._timeout) as s:
            _apply_cookies(s, cookies)
            body, status = await _get_json(s, sub)
            if body is None and status in (403, 451):
                log.info("ozon.l1.composer_403_trying_entrypoint", status=status)
                body, status = await _get_json(s, sub, base=_ENTRYPOINT_BASE)
            if body is None:
                log.warning("ozon.l1.blocked", status=status)
                return None
            ws = body.get("widgetStates") or {}
            payloads = _iter_search_widgets(ws)
            stubs = _stub_offers_from_search(payloads, limit)
            if not stubs:
                # Soft block — 200 OK but no products. Dump enough to
                # diagnose which case (welcome page, region prompt, layout
                # drift) and a sample of widget keys.
                log.warning(
                    "ozon.l1.soft_block_zero_offers",
                    status=status,
                    n_widgets=len(ws),
                    sample_keys=list(ws.keys())[:12],
                    n_search_widgets=len(payloads),
                )
            return stubs

    # -- L2 path --------------------------------------------------------------
    async def _search_l2(
        self,
        query: NormalizedQuery,
        limit: int,
    ) -> list[dict[str, Any]] | None:
        text = query.normalized or query.raw
        body = await fetch_ozon_via_browser(f"/search/?text={quote(text)}&from_global=true")
        if body is None:
            return None
        ws = body.get("widgetStates") or {}
        payloads = _iter_search_widgets(ws)
        stubs = _stub_offers_from_search(payloads, limit)
        if not stubs:
            log.warning(
                "ozon.l2.zero_offers",
                n_widgets=len(ws),
                n_search_widgets=len(payloads),
                sample_keys=list(ws.keys())[:20],
                body_keys=list(body.keys()),
            )
        return stubs

    # -- Enrichment fan-out ---------------------------------------------------
    async def _enrich_all(
        self,
        stubs: list[dict[str, Any]],
        cookies: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Try L1 curl_cffi first (fast). If it didn't backfill name (the
        cheapest indicator that the PDP fetch was blocked), retry that
        stub via the browser. Browser fan-out is bounded by the pool's
        semaphore so we never burn all tabs."""
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:    # pragma: no cover
            return stubs

        async with AsyncSession(impersonate="chrome", timeout=self._timeout) as s:
            _apply_cookies(s, cookies)
            l1_results = await asyncio.gather(
                *(self._enrich_one_l1(s, stub) for stub in stubs),
                return_exceptions=True,
            )

        unblocked: list[dict[str, Any]] = []
        needs_browser: list[dict[str, Any]] = []
        for r in l1_results:
            if isinstance(r, BaseException):
                log.warning("ozon.enrich.l1_crash", error=repr(r))
                continue
            if r.get("name"):
                unblocked.append(r)
            else:
                needs_browser.append(r)

        if needs_browser:
            log.info("ozon.enrich.browser_fallback", n=len(needs_browser))
            browser_results = await asyncio.gather(
                *(self._enrich_one_browser(stub) for stub in needs_browser),
                return_exceptions=True,
            )
            for r in browser_results:
                if isinstance(r, BaseException):
                    log.warning("ozon.enrich.browser_crash", error=repr(r))
                    continue
                unblocked.append(r)

        return unblocked

    async def _enrich_one_l1(
        self,
        session: Any,
        stub: dict[str, Any],
    ) -> dict[str, Any]:
        base_path = urlparse(stub["url"]).path.rstrip("/")

        # Fire all requests in parallel: PDP + all chars containers + reviews.
        # Previously this was a sequential loop (6 round-trips per offer).
        # Now it's one asyncio.gather — total time ≈ slowest single response.
        results = await asyncio.gather(
            _get_json(session, f"{base_path}/"),
            *[
                _get_json(session, f"{base_path}/?layout_container={c}&layout_page_index=2")
                for c in _FULL_CHARS_CONTAINERS
            ],
            _get_json(
                session,
                f"{base_path}/reviews/?layout_container=reviewshelfpaginator"
                f"&layout_page_index=2&page=1"
                f"&sort={_REVIEWS_SORT}&limit={_REVIEWS_PAGE_LIMIT}",
            ),
            return_exceptions=True,
        )

        def _unpack(r: Any) -> tuple[dict[str, Any] | None, int]:
            if isinstance(r, BaseException):
                return None, 0
            return r  # type: ignore[return-value]

        pdp_body, _ = _unpack(results[0])
        chars_bodies = [_unpack(r) for r in results[1:-1]]
        rev_body, _ = _unpack(results[-1])

        chars: dict[str, str] = {}
        if pdp_body:
            ws = pdp_body.get("widgetStates") or {}
            backfill_from_pdp(stub, ws)
            chars.update(chars_via_structural(ws))

        for full_body, _ in chars_bodies:
            if not full_body:
                continue
            ws = full_body.get("widgetStates") or {}
            for name, value in chars_via_structural(ws):
                chars.setdefault(name, value)
            if len(chars) >= self._chars_per_offer:
                break
        stub["characteristics"] = dict(list(chars.items())[:self._chars_per_offer])

        if rev_body:
            stub["reviews"] = extract_reviews(
                rev_body.get("widgetStates") or {}, limit=self._reviews_per_offer,
            )
        else:
            stub.setdefault("reviews", [])
        return stub

    async def _enrich_one_browser(
        self,
        stub: dict[str, Any],
    ) -> dict[str, Any]:
        """Last-resort enrichment via warmed browser. Three parallel
        fetches per offer: main PDP (name/price/image + short chars),
        full-spec container (more chars), reviews (newest first).
        BrowserPool's semaphore bounds total concurrency so 5 offers
        × 3 fetches don't open 15 tabs at once — they queue."""
        base_path = urlparse(stub["url"]).path.rstrip("/")
        pdp_coro = fetch_ozon_via_browser(f"{base_path}/")
        # Single full-chars probe — most stable container per research.
        # Falling through every entry in _FULL_CHARS_CONTAINERS would
        # multiply browser navigations 4×; one is enough here.
        full_chars_coro = fetch_ozon_via_browser(
            f"{base_path}/?layout_container=characteristicsList&layout_page_index=2",
        )
        rev_coro = fetch_ozon_via_browser(
            f"{base_path}/reviews/?layout_container=reviewshelfpaginator"
            f"&layout_page_index=2&page=1"
            f"&sort={_REVIEWS_SORT}&limit={_REVIEWS_PAGE_LIMIT}",
        )
        pdp_body, full_body, rev_body = await asyncio.gather(
            pdp_coro, full_chars_coro, rev_coro, return_exceptions=True,
        )

        chars: dict[str, str] = {}
        if isinstance(pdp_body, dict):
            ws = pdp_body.get("widgetStates") or {}
            backfill_from_pdp(stub, ws)
            for name, value in chars_via_structural(ws):
                chars.setdefault(name, value)
        else:
            if isinstance(pdp_body, BaseException):
                log.debug("ozon.enrich.browser_pdp_failed", error=repr(pdp_body))
        if isinstance(full_body, dict):
            ws_full = full_body.get("widgetStates") or {}
            for name, value in chars_via_structural(ws_full):
                chars.setdefault(name, value)
        stub["characteristics"] = dict(list(chars.items())[:self._chars_per_offer])

        if isinstance(rev_body, dict):
            stub["reviews"] = extract_reviews(
                rev_body.get("widgetStates") or {}, limit=self._reviews_per_offer,
            )
        else:
            stub.setdefault("reviews", [])
            if isinstance(rev_body, BaseException):
                log.debug("ozon.enrich.browser_reviews_failed", error=repr(rev_body))

        return stub

    # -- Stub → ProductOffer --------------------------------------------------
    def _to_product_offer(
        self,
        stub: dict[str, Any],
        now: datetime,
    ) -> ProductOffer | None:
        url = stub.get("url")
        name = stub.get("name") or ""
        price = _price_to_decimal(stub.get("price"))
        if not url or not name or price is None:
            return None    # missing essentials — dropped from the response

        chars: dict[str, str] = stub.get("characteristics") or {}
        # Ozon-specific extras the orchestrator likes to see preserved:
        chars.setdefault("seller", str(stub.get("seller") or ""))
        chars = {k: v for k, v in chars.items() if v}

        try:
            return ProductOffer(
                source=self.source,
                name=name,
                price=price,
                currency="RUB",
                url=url,
                image=stub.get("image") or None,
                images=stub.get("images") or [],
                characteristics=chars,
                seller=stub.get("seller"),
                rating=_safe_float(stub.get("rating")),
                reviews=stub.get("reviews") or [],
                reviews_count=_safe_int(stub.get("reviews_count")),
                fetched_at=now,
                cached=False,
            )
        except Exception as exc:
            log.warning("ozon.offer_validation_failed", error=str(exc), sku=stub.get("sku"))
            return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


__all__ = ["OzonScraper"]
