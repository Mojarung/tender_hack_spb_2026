"""04 — reviews endpoint (`reviewshelfpaginator`).

PURPOSE
    Pull the review list for a single product. The composer-api takes
    a `layout_container=reviewshelfpaginator` argument with paginated
    `?page=N`. The wrapper widgetState keys observed in DevTools:
        webReviewProductScore-*
        webListReviews-*

USAGE
    cd ozon_research
    # Path form (product slug, what cellTrackingInfo.link gives you):
    uv run python 04_reviews_endpoint.py "/product/noutbuk-lenovo-ideapad-1-15amn7-1715567830/"

    # Or pass the SKU as fallback:
    uv run python 04_reviews_endpoint.py 1715567830

NOTES
    - First arg is either a URL path ("/product/.../") or a bare SKU.
    - We dump the FULL widget body so you can browse for the real
      widget keys — they shift between layout versions.
    - This is a separate concern from search-result ratings; here we
      get the actual review TEXT, photo URLs, +/-, helpful counts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import Timer, android_cookies, android_headers, err, info, ok, save_json, section, warn

BASE = "https://api.ozon.ru/composer-api.bx/page/json/v2"


def _product_path(arg: str) -> str:
    """Accept either `/product/slug-or-id/` or a bare SKU."""
    arg = arg.strip()
    if arg.startswith("/product/"):
        return arg.rstrip("/")
    if arg.isdigit():
        return f"/products/{arg}"
    if arg.startswith("http"):
        from urllib.parse import urlparse
        return urlparse(arg).path.rstrip("/")
    return f"/product/{arg.strip('/')}"


def _walk_review_widgets(widget_states: dict) -> dict:
    """Pluck the candidate review widgets so we can save a focused
    snapshot for offline inspection."""
    import orjson

    out: dict = {}
    for key, value in widget_states.items():
        if not isinstance(value, str):
            continue
        if not any(token in key.lower() for token in ("review", "feedback")):
            continue
        try:
            out[key] = orjson.loads(value)
        except orjson.JSONDecodeError:
            out[key] = {"_raw_preview": value[:500]}
    return out


async def main() -> int:
    section("REVIEWS ENDPOINT — /product/.../reviews/?layout_container=reviewshelfpaginator")

    if len(sys.argv) < 2:
        err("usage: python 04_reviews_endpoint.py '/product/slug-or-id/' [page]")
        err("       python 04_reviews_endpoint.py 1715567830 [page]")
        return 3

    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        err("curl_cffi not installed")
        return 3
    import orjson

    base_path = _product_path(sys.argv[1])
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    review_path = f"{base_path}/reviews/?layout_container=reviewshelfpaginator&layout_page_index={page}&page={page}"
    url = f"{BASE}?url={quote(review_path, safe='')}"

    info(f"product = {base_path}")
    info(f"page    = {page}")
    info(f"url     = {url}")

    with Timer() as t:
        try:
            async with AsyncSession(impersonate="chrome131_android", timeout=20) as s:
                for k, v in android_cookies().items():
                    s.cookies.set(k, v)
                resp = await s.get(url, headers=android_headers())
        except Exception as exc:
            err(f"network error: {exc}")
            return 3

    info(f"status  = {resp.status_code} ({t.elapsed_ms} ms)")
    if resp.status_code != 200:
        err("blocked or 404 — check the product path / try via Patchright (09)")
        save_json("04_reviews_block", {"status": resp.status_code, "body_preview": resp.text[:2000]})
        return 1

    try:
        body = orjson.loads(resp.content)
    except orjson.JSONDecodeError:
        err("non-JSON response")
        save_json("04_reviews_nonjson", {"body_preview": resp.text[:2000]})
        return 1

    widget_states = body.get("widgetStates") or {}
    review_widgets = _walk_review_widgets(widget_states)
    if not review_widgets:
        warn("200 OK but no review widgets — wrong slug, no reviews, or layout drift")
        warn(f"available widget keys: {list(widget_states.keys())[:10]}")
        save_json("04_reviews_no_widgets", body)
        return 2

    ok(f"found {len(review_widgets)} review-related widget(s): {list(review_widgets)[:5]}")
    # Drill into the first paginator we find to count actual review items
    for key, w in review_widgets.items():
        items = (w.get("items") if isinstance(w, dict) else None) or []
        if items:
            ok(f"  {key}: {len(items)} item(s) in widget")
            for i, item in enumerate(items[:3], 1):
                snippet = orjson.dumps(item)[:200].decode("utf-8", errors="replace")
                info(f"    item {i}: {snippet}…")
            break

    path_out = save_json("04_reviews_ok", {"page": page, "product": base_path, "widgets": review_widgets})
    ok(f"saved → {path_out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
