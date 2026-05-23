"""10 — Yandex clickthrough fallback (last-resort, low traffic).

PURPOSE
    When BOTH composer-api hosts and the L2 browser are blocked from
    our IP (full WAF lockdown — rare but happens during peak hours),
    we can still grab a few product URLs by:
      1. asking Yandex Search for `site:ozon.ru <query>`
      2. for each result, fetching the product page with `Referer:
         https://yandex.ru/...` — Ozon's anti-bot is much more lenient
         on traffic arriving from the country's main search engine
      3. parsing JSON-LD `Product` schema from the HTML

    JSON-LD on Ozon's PDP includes name, image, price, sku and
    sometimes aggregateRating. Not as rich as composer-api but enough
    to keep the demo alive.

USAGE
    cd ozon_research
    uv run python 10_yandex_clickthrough.py "ноутбук lenovo"

CAVEATS
    - Yandex itself has anti-bot. If you spam this script, you'll need
      to solve a Yandex captcha. Use sparingly — this is a fallback,
      not a primary path.
    - Honest implementation should hit the self-hosted SearXNG that
      the project already runs (scrapers/runet.py uses it). This
      script goes direct to yandex.ru/search/ so it's runnable
      without Docker, but in production swap to SearXNG.
"""

from __future__ import annotations

import asyncio
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from _common import Timer, err, info, ok, query_from_argv, save_json, section, warn

YA_SEARCH = "https://yandex.ru/search/?text={q}"
LIMIT = 5

# Browser-like headers for the Yandex hop (their bot wall hates curl_cffi
# defaults). Plain Chrome desktop.
YA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.5",
}

OZON_HEADERS = {
    "User-Agent": YA_HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.5",
    "Referer": "https://yandex.ru/",
}


class _LinkExtractor(HTMLParser):
    """Grab href URLs from a Yandex SERP that point at ozon.ru/product/."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = next((v for k, v in attrs if k == "href" and v), None)
        if not href:
            return
        if "ozon.ru/product/" in href and href.startswith("http"):
            self.links.append(href)


def _extract_jsonld_products(html: str) -> list[dict]:
    """Pull every <script type="application/ld+json">…Product…</script>."""
    import orjson

    out: list[dict] = []
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        chunk = match.group(1).strip()
        try:
            payload = orjson.loads(chunk)
        except orjson.JSONDecodeError:
            continue
        # Could be a single object, a @graph array, or a list
        candidates = []
        if isinstance(payload, dict):
            candidates = payload.get("@graph") if isinstance(payload.get("@graph"), list) else [payload]
        elif isinstance(payload, list):
            candidates = payload
        for c in candidates:
            if isinstance(c, dict) and c.get("@type") == "Product":
                out.append(c)
    return out


async def main() -> int:
    section("YANDEX CLICKTHROUGH — last-resort, low-volume fallback")

    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        err("curl_cffi not installed")
        return 3
    import orjson

    query = query_from_argv()
    site_query = f"site:ozon.ru {query}"
    url = YA_SEARCH.format(q=quote(site_query))
    info(f"yandex query = {site_query!r}")
    info(f"url          = {url}")

    async with AsyncSession(impersonate="chrome131", timeout=20) as s:
        with Timer() as t:
            try:
                ya_resp = await s.get(url, headers=YA_HEADERS)
            except Exception as exc:
                err(f"yandex network error: {exc}")
                return 3

        info(f"yandex status = {ya_resp.status_code} ({t.elapsed_ms} ms)")
        if ya_resp.status_code != 200:
            err(f"yandex blocked (HTTP {ya_resp.status_code}) — try in a browser to clear the captcha")
            return 1

        parser = _LinkExtractor()
        try:
            parser.feed(ya_resp.text)
        except Exception as exc:
            warn(f"html parse partial: {exc}")
        # De-dup by ozon path (Yandex repeats /url?...)
        seen: set[str] = set()
        product_urls: list[str] = []
        for raw in parser.links:
            path = urlparse(raw).path
            if "/product/" not in path or path in seen:
                continue
            seen.add(path)
            product_urls.append(raw)
            if len(product_urls) >= LIMIT:
                break

        if not product_urls:
            warn("no ozon product URLs in the SERP — try a more specific query")
            return 2
        ok(f"got {len(product_urls)} product URL(s) from Yandex")

        # Fetch each PDP and pull JSON-LD
        results: list[dict] = []
        for i, p_url in enumerate(product_urls, 1):
            await asyncio.sleep(0.6)
            try:
                resp = await s.get(p_url, headers=OZON_HEADERS)
            except Exception as exc:
                warn(f"  [{i}] {p_url}: network error {exc}")
                continue
            if resp.status_code != 200:
                warn(f"  [{i}] {p_url}: HTTP {resp.status_code}")
                continue
            products = _extract_jsonld_products(resp.text)
            if not products:
                warn(f"  [{i}] {p_url}: no JSON-LD Product found")
                continue
            prod = products[0]
            results.append({
                "url": p_url,
                "name": prod.get("name"),
                "sku": prod.get("sku") or prod.get("productID"),
                "image": prod.get("image"),
                "price": ((prod.get("offers") or {}).get("price")
                          if isinstance(prod.get("offers"), dict) else None),
                "rating": ((prod.get("aggregateRating") or {}).get("ratingValue")
                           if isinstance(prod.get("aggregateRating"), dict) else None),
                "raw_jsonld": prod,
            })
            ok(f"  [{i}] {(prod.get('name') or '')[:60]}")

    if not results:
        err("Yandex clickthrough produced 0 products. Stack is fully blocked.")
        return 2

    path = save_json("10_yandex_clickthrough_ok", {"query": query, "products": results})
    ok(f"saved → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
