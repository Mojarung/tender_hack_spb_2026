"""13 — warm cookies → curl_cffi: the hybrid fast path.

PURPOSE
    Once 12_nodriver_pro.py has solved the challenge ONCE and dumped
    `_out/ozon_cookies.json`, we don't need to pay the browser cost
    again. This script loads those cookies into curl_cffi and runs the
    same full-pipeline workflow (search → 5 products → chars + reviews)
    at HTTP-only speed (~3-4 s end-to-end vs ~30 s for the browser).

    Re-run 12 whenever this stops working (cookies eventually expire
    or get rotated by Ozon — typically 24-72 h).

USAGE
    cd ozon_research
    # First time only — get cookies:
    uv run python 12_nodriver_pro.py "ноутбук lenovo"
    # Then every subsequent query:
    uv run python 13_warm_cookies_to_curl.py "ноутбук lenovo"
    uv run python 13_warm_cookies_to_curl.py "шины 205 55 R16"
    ...

WHY THIS WORKS
    Ozon's anti-bot WAF gates on:
      * IP reputation     — same IP across runs, fine
      * JA3/JA4 TLS       — curl_cffi `chrome` profile passes
      * Behavioural score — there isn't one for HTTP-only fetches,
                            ONCE the WAF has decided you're human (the
                            initial browser session did that)
      * Cookie continuity — abt_data + __Secure-ext_xcid + __Secure-...
                            tokens from a real browser session = pass

EXIT CODES
    0 — got enriched offers
    1 — search blocked (cookies stale → re-run 12)
    2 — search OK but no offers parsed
    3 — cookies file missing
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from _common import OUT_DIR, Timer, err, info, ok, query_from_argv, save_json, section, warn

COOKIES_PATH = OUT_DIR / "ozon_cookies.json"
BASE = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"

LIMIT = 5
REVIEWS_PER_PRODUCT = 3
PACE_MIN_MS = 300
PACE_MAX_MS = 800

# Desktop browser headers — must match the user-agent of the browser
# that warmed the cookies. We use a generic recent Chrome desktop UA;
# this matches curl_cffi `impersonate='chrome'` (latest stable).
HEADERS = {
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


# Walkers reused from 12 — kept inline so this file is standalone
def _walk_search(widget_states):
    import orjson
    out = []
    for k, v in widget_states.items():
        if isinstance(v, str) and k.startswith(("searchResultsV2", "tileGridDesktop", "skuList")):
            try:
                out.append(orjson.loads(v))
            except orjson.JSONDecodeError:
                pass
    return out


def _extract_offers(payloads, limit):
    offers, seen = [], set()
    for p in payloads:
        for item in p.get("items") or []:
            t = (item.get("cellTrackingInfo") or {}).get("product") or {}
            sku = str(t.get("id") or item.get("itemId") or "")
            if sku and sku in seen:
                continue
            link = t.get("link") or (item.get("action") or {}).get("link") or item.get("link")
            if not link:
                continue
            if link.startswith("/"):
                link = f"https://www.ozon.ru{link}"
            offers.append({
                "sku": sku,
                "name": t.get("title"),
                "price": t.get("finalPrice") or t.get("price"),
                "image": t.get("imageUrl") or t.get("image"),
                "url": link,
                "rating": t.get("rating"),
                "reviews_count": t.get("reviewsCount"),
                "seller": t.get("sellerName"),
            })
            if sku:
                seen.add(sku)
            if len(offers) >= limit:
                return offers
    return offers


def _walk_widgets(widget_states, tokens):
    import orjson
    out = {}
    for k, v in widget_states.items():
        if isinstance(v, str) and any(t in k.lower() for t in tokens):
            try:
                out[k] = orjson.loads(v)
            except orjson.JSONDecodeError:
                pass
    return out


def _flatten_attrs(widgets):
    pairs, seen = [], set()

    def _v(node):
        if isinstance(node, dict):
            name = node.get("name") or node.get("title") or node.get("label")
            values = node.get("values") or node.get("value") or node.get("texts")
            if isinstance(name, str) and values:
                if isinstance(values, list):
                    text = ", ".join(v.get("text", str(v)) if isinstance(v, dict) else str(v) for v in values)
                elif isinstance(values, dict):
                    text = values.get("text") or str(values)
                else:
                    text = str(values)
                if name.strip() and text.strip():
                    pair = (name.strip(), text.strip())
                    if pair not in seen:
                        seen.add(pair)
                        pairs.append(pair)
            for v in node.values():
                _v(v)
        elif isinstance(node, list):
            for v in node:
                _v(v)
    _v(widgets)
    return pairs


def _extract_reviews(widgets, limit):
    out = []

    def _r(item):
        if not isinstance(item, dict):
            return None
        text = (
            item.get("text") or item.get("comment") or item.get("body")
            or ((item.get("review") or {}).get("text") if isinstance(item.get("review"), dict) else None)
        )
        if not text:
            return None
        author = item.get("author") or item.get("authorName") or item.get("userName")
        if isinstance(author, dict):
            author = author.get("name") or author.get("title")
        score = item.get("score") or item.get("rating") or item.get("itemRating")
        return {"author": author, "score": score, "text": text[:600]}

    def _v(node):
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            r = _r(node)
            if r:
                out.append(r)
                if len(out) >= limit:
                    return
            for v in node.values():
                _v(v)
        elif isinstance(node, list):
            for v in node:
                _v(v)
    _v(widgets)
    return out[:limit]


def _load_cookies() -> list[dict]:
    if not COOKIES_PATH.exists():
        return []
    try:
        return json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


async def _pace():
    await asyncio.sleep(random.uniform(PACE_MIN_MS, PACE_MAX_MS) / 1000)


async def _get_json(session, sub_path: str):
    import orjson
    url = f"{BASE}?url={quote(sub_path, safe='')}"
    resp = await session.get(url, headers=HEADERS)
    if resp.status_code != 200:
        return None, resp.status_code
    try:
        return orjson.loads(resp.content), 200
    except orjson.JSONDecodeError:
        return None, 200


async def main() -> int:
    section("HYBRID — warm cookies in curl_cffi (no browser cost)")

    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        err("curl_cffi not installed — `uv sync` in this folder")
        return 3

    cookies = _load_cookies()
    if not cookies:
        err(f"no cookies at {COOKIES_PATH}")
        err("→ run 12_nodriver_pro.py first to dump them")
        return 3

    ozon_cookies = [c for c in cookies if c.get("domain") and "ozon" in c["domain"]]
    info(f"loaded {len(ozon_cookies)} ozon.ru cookie(s) from {COOKIES_PATH.name}")
    # Show the load-bearing ones so the user can see they're present
    key_names = {"abt_data", "__Secure-ext_xcid", "__Secure-access-token", "__Secure-refresh-token", "is_cookies_accepted"}
    present = sorted({c["name"] for c in ozon_cookies if c.get("name") in key_names})
    if present:
        info(f"  load-bearing: {', '.join(present)}")
    else:
        warn("  no abt_data / __Secure-* cookies — fast path may not work, rerun 12")

    query = query_from_argv()
    info(f"query    = {query!r}")

    async with AsyncSession(impersonate="chrome", timeout=15) as s:
        for c in ozon_cookies:
            try:
                s.cookies.set(
                    c["name"], c["value"],
                    domain=c.get("domain") or ".ozon.ru",
                    path=c.get("path") or "/",
                )
            except Exception:
                pass

        # Search
        with Timer() as t_search:
            body, status = await _get_json(s, f"/search/?text={quote(query)}&from_global=true")
        if not body:
            err(f"search blocked (HTTP {status}) — cookies likely expired")
            err("→ re-run 12_nodriver_pro.py to refresh cookies")
            return 1
        offers = _extract_offers(_walk_search(body.get("widgetStates") or {}), LIMIT)
        if not offers:
            warn("search 200 but no offers")
            save_json("13_warm_no_offers", body)
            return 2
        ok(f"search OK in {t_search.elapsed_ms} ms — {len(offers)} offer(s)")

        # Enrichment
        enriched = []
        for i, offer in enumerate(offers, 1):
            base_path = urlparse(offer["url"]).path.rstrip("/")
            with Timer() as t:
                await _pace()
                chars_body, _ = await _get_json(
                    s, f"{base_path}/?layout_container=pdpAtomicCharacteristics&layout_page_index=2"
                )
                chars = []
                if chars_body:
                    chars = _flatten_attrs(_walk_widgets(
                        chars_body.get("widgetStates") or {},
                        ("characteristic", "attribute", "shortcharacter", "techspec"),
                    ))[:30]

                await _pace()
                rev_body, _ = await _get_json(
                    s, f"{base_path}/reviews/?layout_container=reviewshelfpaginator&layout_page_index=2&page=1"
                )
                reviews = []
                if rev_body:
                    reviews = _extract_reviews(_walk_widgets(
                        rev_body.get("widgetStates") or {}, ("review", "feedback"),
                    ), REVIEWS_PER_PRODUCT)

            full = dict(offer)
            full["characteristics"] = chars
            full["reviews"] = reviews
            enriched.append(full)
            info(f"  [{i}/{len(offers)}] {(offer.get('name') or '')[:60]} — "
                 f"{len(chars)} chars, {len(reviews)} reviews ({t.elapsed_ms} ms)")

    section("Summary")
    enriched_count = sum(1 for e in enriched if e.get("characteristics") or e.get("reviews"))
    ok(f"enriched: {enriched_count}/{len(enriched)}")
    path = save_json("13_warm_ok", {"query": query, "offers": enriched})
    ok(f"saved → {path}")
    return 0 if enriched_count else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
