"""Ozon adapter — mobile composer-api with `ozonapp_android` UA.

Strategy (free-mode L1):
    POST headers as an Android app → bypasses most of the Cloudflare /
    custom anti-bot challenges aimed at desktop browsers. Uses
    curl_cffi with TLS impersonation of Chrome 131 so JA4 matches a
    real client.

If we still get 403 / antibot redirect → caller is expected to
escalate to L2 (Patchright stealth browser) via the cascade router.
See backend/docs/anti-bot.md §5.2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

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

_BASE_URL = "https://api.ozon.ru/composer-api.bx/page/json/v2"
_APP_VERSION = "17.48.0"
_APP_BUILD = "2528"

_HEADERS = {
    "User-Agent": f"ozonapp_android/{_APP_VERSION}+{_APP_BUILD}",
    "x-o3-app-name": "ozonapp_android",
    "x-o3-app-version": _APP_VERSION,
    "x-o3-device-type": "mobile",
    "Accept": "application/json; charset=utf-8",
    "Accept-Language": "ru",
    "Host": "api.ozon.ru",
}


def _price_from_text(text: str) -> Decimal | None:
    """Parse Ozon-shaped price like '69 990 ₽' or '69 990\xa0₽'."""
    cleaned = "".join(ch for ch in text if ch.isdigit())
    if not cleaned:
        return None
    return Decimal(cleaned)


def _iter_search_widgets(layout_widgets: dict[str, Any]) -> list[dict[str, Any]]:
    """Find every `widgetStates` key that holds search-result items.

    Ozon names them like `searchResultsV2-...`, `tileGridDesktop-...`,
    sometimes `skuList-...`. Each value is a JSON STRING, so we parse it
    a second time.
    """
    out: list[dict[str, Any]] = []
    for key, value in layout_widgets.items():
        if not isinstance(value, str):
            continue
        if not (key.startswith(("searchResultsV2", "tileGridDesktop", "skuList"))):
            continue
        try:
            payload = orjson.loads(value)
        except orjson.JSONDecodeError:
            continue
        out.append(payload)
    return out


def _extract_offers(widget_payloads: list[dict[str, Any]], limit: int) -> list[ProductOffer]:
    """Walk widget payloads and synthesize ProductOffer rows.

    Ozon's atomic schema is verbose. We look for items[].mainState[] arrays
    where each entry has `atom.text` (or `atom.textRenderer.text`). The
    item link is usually under items[].action.link or items[].cellTrackingInfo.product.link.
    """
    offers: list[ProductOffer] = []
    seen_ids: set[str] = set()
    now = datetime.now(tz=UTC)

    for payload in widget_payloads:
        items = payload.get("items") or []
        for item in items:
            tracking = item.get("cellTrackingInfo", {}).get("product", {}) or {}
            sku_id = str(tracking.get("id") or item.get("itemId") or "")
            if sku_id and sku_id in seen_ids:
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

            title = tracking.get("title") or ""
            price_value = tracking.get("finalPrice") or tracking.get("price")
            image_url = tracking.get("imageUrl") or tracking.get("image")

            # Fallback: scan mainState atoms for price/title text.
            if not title or price_value is None:
                for state in item.get("mainState") or []:
                    atom = state.get("atom") or {}
                    text = atom.get("text") or (atom.get("textRenderer") or {}).get("text")
                    if not text:
                        continue
                    if "₽" in text and price_value is None:
                        price_value = _price_from_text(text)
                    elif not title and len(text) > 8 and "₽" not in text:
                        title = text

            if not title or price_value is None:
                continue

            price_dec = (
                price_value if isinstance(price_value, Decimal)
                else _price_from_text(str(price_value))
            )
            if price_dec is None:
                continue

            offer = ProductOffer(
                source=SourceKind.OZON,
                name=title,
                price=price_dec,
                currency="RUB",
                url=link,
                image=image_url,
                characteristics={
                    "rating": str(tracking.get("rating") or ""),
                    "reviews": str(tracking.get("reviewsCount") or ""),
                    "seller": str(tracking.get("sellerName") or ""),
                },
                seller=tracking.get("sellerName"),
                rating=float(tracking["rating"]) if tracking.get("rating") else None,
                fetched_at=now,
                cached=False,
            )
            offers.append(offer)
            if sku_id:
                seen_ids.add(sku_id)
            if len(offers) >= limit:
                return offers
    return offers


class OzonScraper:
    source: SourceKind = SourceKind.OZON

    def __init__(self, timeout_s: float = 10.0, *, enable_l2: bool = True) -> None:
        self._timeout = timeout_s
        # L2 escalation — drive the nodriver stealth browser when the L1
        # mobile API is blocked. Disable in tests / when nodriver is absent.
        self._enable_l2 = enable_l2

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        """L1 mobile API, escalating to the L2 stealth browser on a block."""
        result = await self._search_l1(query, limit, on_offer=on_offer)
        if result.offers or not self._enable_l2:
            return result
        log.info("ozon.escalate_l2", l1_error=result.error)
        try:
            l2 = await self._search_l2(query, limit, on_offer=on_offer)
        except Exception as exc:
            log.warning("ozon.l2_failed", error=str(exc))
            return result
        return l2 if l2.offers else result

    async def _search_l2(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        """L2 — fetch the composer-api through the stealth browser."""
        from pricepulse.antibot.browser_fetch import fetch_ozon_composer

        body = await fetch_ozon_composer(query.normalized or query.raw)
        if body is None:
            return ScrapeResult(
                source=self.source, offers=[], error="ozon L2 fetch failed",
            )
        payloads = _iter_search_widgets(body.get("widgetStates") or {})
        offers = _extract_offers(payloads, limit=limit)
        if on_offer is not None:
            for o in offers:
                await on_offer(o)
        scrape_requests_total.labels(
            source=self.source.value, outcome="ok", proxy_tier="browser",
        ).inc()
        scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
        log.info("ozon.l2_ok", returned=len(offers))
        return ScrapeResult(source=self.source, offers=offers)

    async def _search_l1(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        # Lazy-import — curl_cffi pulls a native lib we don't want at import-time.
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:    # pragma: no cover
            return ScrapeResult(
                source=self.source, offers=[], error="curl_cffi not installed"
            )

        path = f"/search/?text={quote(query.normalized or query.raw)}&from_global=true"
        url = f"{_BASE_URL}?url={quote(path, safe='')}"

        with scrape_duration_seconds.labels(source=self.source.value).time():
            try:
                async with AsyncSession(impersonate="chrome131", timeout=self._timeout) as s:
                    resp = await s.get(url, headers=_HEADERS)
            except Exception as exc:  # curl_cffi raises generic errors
                scrape_requests_total.labels(
                    source=self.source.value, outcome="timeout", proxy_tier="none",
                ).inc()
                log.warning("ozon.fetch_failed", error=str(exc))
                return ScrapeResult(
                    source=self.source, offers=[], error=f"ozon fetch failed: {exc}"
                )

            if resp.status_code != 200:
                outcome = "blocked" if resp.status_code in (403, 451) else "http_4xx"
                scrape_requests_total.labels(
                    source=self.source.value, outcome=outcome, proxy_tier="none",
                ).inc()
                log.warning("ozon.bad_status", status=resp.status_code)
                return ScrapeResult(
                    source=self.source, offers=[], error=f"ozon HTTP {resp.status_code}"
                )

            try:
                body = orjson.loads(resp.content)
            except orjson.JSONDecodeError:
                scrape_requests_total.labels(
                    source=self.source.value, outcome="http_5xx", proxy_tier="none",
                ).inc()
                return ScrapeResult(
                    source=self.source, offers=[], error="ozon non-json response"
                )

            widget_states = body.get("widgetStates") or {}
            payloads = _iter_search_widgets(widget_states)
            offers = _extract_offers(payloads, limit=limit)
            if on_offer is not None:
                for o in offers:
                    await on_offer(o)
            scrape_requests_total.labels(
                source=self.source.value, outcome="ok", proxy_tier="none",
            ).inc()
            scrape_offers_returned_total.labels(source=self.source.value).inc(len(offers))
            log.info("ozon.ok", returned=len(offers))
            return ScrapeResult(source=self.source, offers=offers)
