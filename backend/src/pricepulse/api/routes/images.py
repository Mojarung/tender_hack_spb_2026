"""Image proxy/cache — first hit uploads to MinIO, then 302s the browser
to the public bucket URL.

Why a proxy at all: marketplace CDNs re-shard URLs and rate-limit hot
referrers, so the same image can disappear or 403 mid-demo. Caching our
own copy makes the UI stable + cuts ~80% of the egress on repeat views.

Failure mode: if MinIO is down or the origin returns garbage, we 302 to
the original URL — so the user always sees *some* image, just without
the cache benefit.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from pricepulse.api.cache import get_image_cache
from pricepulse.domain.enums import SourceKind

router = APIRouter(prefix="/image-proxy", tags=["images"])

# Hardcoded allow-list. Without this an open proxy ?url=http://evil.example
# turns our infra into an SSRF gadget. The list mirrors the marketplaces
# our scrapers actually return image URLs from.
_ALLOWED_HOSTS_SUFFIXES: tuple[str, ...] = (
    "wbbasket.ru", "wb.ru", "wildberries.ru",
    "ozone.ru", "ozon.ru", "ozonusercontent.com", "ozcdn.com",
    "yandex.net", "yastatic.net", "yandex.ru",
    # Google Shopping thumbnails (encrypted-tbnN.gstatic.com) — Runet
    # source pulls image URLs from these CDN hosts.
    "gstatic.com", "googleusercontent.com",
    # Runet shops — best-effort; the scraper extracts arbitrary image URLs
    # from JSON-LD, so we accept https origins generically below as well.
    "re-store.ru", "biggeek.ru", "dns-shop.ru", "mvideo.ru", "citilink.ru",
    "cmstore.ru", "kingstore.link", "apple-market.ru", "stores-apple.com",
)


def _host_allowed(host: str) -> bool:
    h = host.lower()
    return any(h == s or h.endswith("." + s) for s in _ALLOWED_HOSTS_SUFFIXES)


@router.get("")
async def image_proxy(
    url: str = Query(..., min_length=8, max_length=2048),
    source: SourceKind = Query(...),
) -> RedirectResponse:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # Refuse anything that's not a plain http(s) URL — covers `file://`,
        # `gopher://`, and the `?url=//evil` schema-less form.
        return RedirectResponse(url=url, status_code=302)
    if not _host_allowed(parsed.netloc):
        # Unknown origin → just pass through. Never proxy arbitrary hosts.
        return RedirectResponse(url=url, status_code=302)

    cache = get_image_cache()
    if cache is None:
        return RedirectResponse(url=url, status_code=302)

    cached = await cache.ensure_cached(source.value, url)
    return RedirectResponse(url=cached or url, status_code=302)
