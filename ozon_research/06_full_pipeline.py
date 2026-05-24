"""06 — FULL PIPELINE: search → 5 products with images + chars + reviews.

PURPOSE
    This is what the hackathon demo needs from Ozon: one query → five
    products → for each product: image, top-N characteristics, top-N
    reviews. All L1 (mobile composer-api), no browser, no proxies.

    Pacing:
      * 1 search request
      * For each of the 5 SKUs: 1 characteristics request + 1 reviews
        request (= 10 follow-ups)
      * Random 250-700 ms delay between requests (anti-rate-limit)
      * Single curl_cffi session — cookies persist (abt_data, ext_xcid)

    Mid-2024 anecdote (JTJag/ozon-sellers-parser): 46 000 requests in
    <2 h on a single non-residential IP, no 403s. Our 11 requests per
    query is well inside that envelope.

USAGE
    cd ozon_research
    uv run python 06_full_pipeline.py "ноутбук lenovo"

OUTPUT
    A consolidated JSON in _out/ with shape:
        {
          "query": "...",
          "offers": [
            {"sku", "name", "price", "image", "url",
             "characteristics": [[name, value], ...],
             "reviews": [{author, score, text, ...}, ...]},
            ...x5
          ]
        }

EXIT CODES
    0 — all 5 enriched (or fewer if search returned fewer)
    1 — search blocked
    2 — search OK but every enrichment failed
"""

from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

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

LIMIT = 5
REVIEWS_PER_PRODUCT = 3
PACE_MIN_MS = 250
PACE_MAX_MS = 700


# ---- mini parsers reused from 02 / 04 / 05 ---------------------------------
def _walk_search(widget_states: dict) -> list[dict]:
    import orjson

    out: list[dict] = []
    for key, value in widget_states.items():
        if not isinstance(value, str):
            continue
        if not key.startswith(("searchResultsV2", "tileGridDesktop", "skuList")):
            continue
        try:
            out.append(orjson.loads(value))
        except orjson.JSONDecodeError:
            continue
    return out


def _extract_offers(payloads, limit):
    offers, seen = [], set()
    for payload in payloads:
        for item in payload.get("items") or []:
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
        if not isinstance(v, str):
            continue
        kl = k.lower()
        if not any(t in kl for t in tokens):
            continue
        try:
            out[k] = orjson.loads(v)
        except orjson.JSONDecodeError:
            continue
    return out


def _flatten_attrs(widgets):
    pairs, seen = [], set()

    def _visit(node):
        if isinstance(node, dict):
            name = node.get("name") or node.get("title") or node.get("label")
            values = node.get("values") or node.get("value") or node.get("texts")
            if isinstance(name, str) and values:
                if isinstance(values, list):
                    text = ", ".join(
                        v.get("text", str(v)) if isinstance(v, dict) else str(v) for v in values
                    )
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
                _visit(v)
        elif isinstance(node, list):
            for v in node:
                _visit(v)

    _visit(widgets)
    return pairs


def _extract_reviews(widgets, limit):
    """Best-effort extractor — reviews can sit under items[].comment, .reviewText, .author etc."""
    out = []

    def _maybe_review(item):
        if not isinstance(item, dict):
            return None
        text = (
            item.get("text")
            or item.get("comment")
            or item.get("body")
            or ((item.get("review") or {}).get("text") if isinstance(item.get("review"), dict) else None)
        )
        if not text:
            return None
        author = item.get("author") or item.get("authorName") or item.get("userName")
        if isinstance(author, dict):
            author = author.get("name") or author.get("title")
        score = item.get("score") or item.get("rating") or item.get("itemRating")
        return {"author": author, "score": score, "text": text[:600]}

    def _visit(node):
        if isinstance(node, dict):
            r = _maybe_review(node)
            if r:
                out.append(r)
                if len(out) >= limit:
                    return
            for v in node.values():
                _visit(v)
                if len(out) >= limit:
                    return
        elif isinstance(node, list):
            for v in node:
                _visit(v)
                if len(out) >= limit:
                    return

    _visit(widgets)
    return out[:limit]


# ---- core flow -------------------------------------------------------------
async def _pace():
    await asyncio.sleep(random.uniform(PACE_MIN_MS, PACE_MAX_MS) / 1000)


async def _get_json(session, sub_path: str):
    import orjson

    url = f"{BASE}?url={quote(sub_path, safe='')}"
    resp = await session.get(url, headers=android_headers())
    if resp.status_code != 200:
        return None, resp.status_code
    try:
        return orjson.loads(resp.content), 200
    except orjson.JSONDecodeError:
        return None, 200


async def _enrich(session, offer):
    """Fetch chars + reviews for a single offer. Tolerant of failures."""
    base_path = urlparse(offer["url"]).path.rstrip("/")
    enriched = dict(offer)

    # --- characteristics ---
    await _pace()
    char_body, _ = await _get_json(
        session, f"{base_path}/?layout_container=pdpAtomicCharacteristics&layout_page_index=2"
    )
    if char_body:
        chars = _flatten_attrs(_walk_widgets(
            char_body.get("widgetStates") or {},
            ("characteristic", "attribute", "shortcharacter", "techspec"),
        ))
        enriched["characteristics"] = chars[:30]
    else:
        enriched["characteristics"] = []

    # --- reviews ---
    await _pace()
    rev_body, _ = await _get_json(
        session, f"{base_path}/reviews/?layout_container=reviewshelfpaginator&layout_page_index=2&page=1"
    )
    if rev_body:
        rev_widgets = _walk_widgets(
            rev_body.get("widgetStates") or {},
            ("review", "feedback"),
        )
        enriched["reviews"] = _extract_reviews(rev_widgets, REVIEWS_PER_PRODUCT)
    else:
        enriched["reviews"] = []

    return enriched


async def main() -> int:
    section("FULL PIPELINE — search → 5 products × (chars + reviews)")

    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        err("curl_cffi not installed")
        return 3
    import orjson

    query = query_from_argv()
    info(f"query  = {query!r}")
    info(f"limit  = {LIMIT} products, {REVIEWS_PER_PRODUCT} reviews each")
    info(f"pace   = {PACE_MIN_MS}-{PACE_MAX_MS} ms random between requests")

    cookies = android_cookies()

    async with AsyncSession(impersonate="chrome131_android", timeout=20) as s:
        for k, v in cookies.items():
            s.cookies.set(k, v)

        # 1) search
        with Timer() as t_search:
            search_path = f"/search/?text={quote(query)}&from_global=true"
            body, status = await _get_json(s, search_path)
        if not body:
            err(f"search blocked (HTTP {status}) — try 03 (entrypoint) or 09 (Patchright)")
            return 1
        offers = _extract_offers(_walk_search(body.get("widgetStates") or {}), LIMIT)
        if not offers:
            warn("search 200 but no offers")
            save_json("06_no_offers", body)
            return 2
        ok(f"search OK in {t_search.elapsed_ms} ms — got {len(offers)} offer(s)")

        # 2) enrich each
        enriched: list[dict] = []
        for i, offer in enumerate(offers, 1):
            with Timer() as t:
                e = await _enrich(s, offer)
            chars_n = len(e.get("characteristics") or [])
            reviews_n = len(e.get("reviews") or [])
            info(f"  [{i}/{len(offers)}] {(e.get('name') or '')[:60]} — "
                 f"{chars_n} chars, {reviews_n} reviews ({t.elapsed_ms} ms)")
            enriched.append(e)

    # 3) summarise
    section("Summary")
    enriched_count = sum(1 for e in enriched if e.get("characteristics") or e.get("reviews"))
    ok(f"enriched: {enriched_count}/{len(enriched)}")

    path = save_json("06_full_pipeline_ok", {"query": query, "offers": enriched})
    ok(f"saved → {path}")
    return 0 if enriched_count else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
