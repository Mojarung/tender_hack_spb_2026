"""02 — L1 hardened (mobile composer-api, full header set).

PURPOSE
    This is the upgrade path for backend/src/pricepulse/scrapers/ozon.py
    that closes the gap with public production scrapers
    (Churkashh/ozon-pinneaples, JTJag/ozon-sellers-parser — both ran
    tens of thousands of requests through this exact header shape, on a
    single non-residential IP, without 403s).

DIFFERENCES from 01:
    + MOBILE-GAID (random UUIDv4 — Google Advertising ID)
    + MOBILE-LAT  (0  — location tracking opt-out)
    + x-o3-fp     (17-hex device fingerprint, "1." prefix)
    + x-o3-sample-trace ("false")
    + abt_data    cookie (rotated per session)
    + x-o3-app-name cookie (mirrors the header)
    + impersonate="chrome131_android"  (TLS that MATCHES the mobile UA)

Five products are extracted at the end so you can eyeball that the
shape matches what production parses.

USAGE
    cd ozon_research
    uv run python 02_l1_hardened.py "ноутбук lenovo"

EXIT CODES
    0  — got >=1 offer
    1  — non-200 HTTP
    2  — 200 but soft-block (no widgets)
    3  — network/import error
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    android_cookies,
    android_headers,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

BASE = "https://api.ozon.ru/composer-api.bx/page/json/v2"


def _iter_search_widgets(layout_widgets: dict) -> list[dict]:
    """Same idiom as scrapers/ozon.py — search widgets are stringified JSON."""
    import orjson

    out: list[dict] = []
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


def _extract_offers(payloads: list[dict], limit: int = 5) -> list[dict]:
    seen: set[str] = set()
    offers: list[dict] = []
    for payload in payloads:
        for item in payload.get("items") or []:
            tracking = (item.get("cellTrackingInfo") or {}).get("product") or {}
            sku = str(tracking.get("id") or item.get("itemId") or "")
            if sku and sku in seen:
                continue

            link = tracking.get("link") or (item.get("action") or {}).get("link") or item.get("link")
            if not link:
                continue
            if link.startswith("/"):
                link = f"https://www.ozon.ru{link}"

            offers.append({
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
            if len(offers) >= limit:
                return offers
    return offers


TLS_CASCADE = ["chrome131_android", "chrome131", "chrome", "safari17_2_ios"]


async def _try_one(tls: str, url: str, headers: dict, cookies: dict, timeout: float = 15.0):
    """Returns (resp, elapsed_ms, exc_or_none)."""
    from curl_cffi.requests import AsyncSession

    with Timer() as t:
        try:
            async with AsyncSession(impersonate=tls, timeout=timeout) as s:
                for k, v in cookies.items():
                    s.cookies.set(k, v)
                resp = await s.get(url, headers=headers)
                return resp, t.elapsed_ms, None
        except Exception as exc:
            return None, t.elapsed_ms, exc


async def main() -> int:
    section("L1 HARDENED — Churkashh/JTJag header shape, TLS-profile cascade")

    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        err("curl_cffi not installed")
        return 3

    import orjson

    query = query_from_argv()
    info(f"query  = {query!r}")
    path = f"/search/?text={quote(query)}&from_global=true"
    url = f"{BASE}?url={quote(path, safe='')}"

    headers = android_headers()
    cookies = android_cookies()
    info(f"hdrs   = {len(headers)} (added MOBILE-GAID/MOBILE-LAT/x-o3-fp/x-o3-sample-trace)")
    info(f"cookies= abt_data ({len(cookies['abt_data'])}c) + x-o3-app-name")
    info(f"cascade= {TLS_CASCADE}  (first 200 wins)")

    resp = None
    chosen_tls: str | None = None
    elapsed_ms = 0
    last_status: int | None = None
    failures: list[dict] = []

    for tls in TLS_CASCADE:
        info(f"--- trying impersonate={tls!r} ...")
        candidate, ms, exc = await _try_one(tls, url, headers, cookies)
        if exc is not None:
            warn(f"    network error: {exc}")
            failures.append({"tls": tls, "error": repr(exc)})
            continue
        info(f"    HTTP {candidate.status_code}  ({ms} ms)  bytes={len(candidate.content)}")
        last_status = candidate.status_code
        if candidate.status_code == 200:
            try:
                orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                warn("    200 but non-JSON — anti-bot stub, trying next TLS")
                failures.append({
                    "tls": tls, "status": 200, "body_preview": candidate.text[:300],
                    "headers": dict(candidate.headers),
                })
                continue
            resp = candidate
            chosen_tls = tls
            elapsed_ms = ms
            break
        failures.append({
            "tls": tls, "status": candidate.status_code,
            "body_preview": candidate.text[:300],
            "headers": dict(candidate.headers),
        })

    if resp is None:
        err(f"all TLS profiles failed (last HTTP {last_status})")
        err("→ run 11_diagnose.py to inspect, or jump to 12_nodriver_pro.py")
        save_json("02_hardened_block_cascade", {"failures": failures})
        return 1
    ok(f"WIN — TLS profile {chosen_tls!r} passed in {elapsed_ms} ms")

    body = orjson.loads(resp.content)
    payloads = _iter_search_widgets(body.get("widgetStates") or {})
    offers = _extract_offers(payloads, limit=5)
    if not offers:
        warn("200 but no offers parsed — check widget keys / structure drift")
        save_json("02_hardened_no_offers", body)
        return 2

    ok(f"got {len(offers)} offers")
    for i, o in enumerate(offers, 1):
        price = o.get("price")
        info(f"  {i}. {(o.get('name') or '')[:60]}... — {price} ₽")
        info(f"     {o.get('url')}")

    path_out = save_json("02_hardened_ok", {"offers": offers, "raw_body": body})
    ok(f"saved → {path_out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
