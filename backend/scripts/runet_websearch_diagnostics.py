"""Live diagnostics for the Runet/web-search source.

Run from ``backend/``:
    uv run python scripts/runet_websearch_diagnostics.py

The script intentionally hits real websites. Use it as a manual diagnostic, not
as a CI test.
"""

from __future__ import annotations

import argparse
import asyncio
import html as html_lib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from curl_cffi.requests import AsyncSession

from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers.runet import RunetScraper, _is_product, _to_offer, _tokenize, _walk_jsonld

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


RUNET_QUERIES: list[tuple[str, str]] = [
    ("tyres", "шины 205/55 R16 зимние шипованные"),
    ("tyres", "шины 225/45 R17 летние"),
    ("tyres", "шины R15 зимние"),
    ("office", "принтер лазерный цветной wifi"),
    ("office", "картридж Canon 725"),
    ("office", "бумага A4 80 г/м2 500 листов"),
    ("apparel", "футболка мужская хлопок черная размер M"),
    ("apparel", "кроссовки nike мужские 42"),
    ("apparel", "куртка женская зимняя"),
    ("electronics", "iphone 15 128 черный"),
    ("electronics", "samsung galaxy s24 256"),
    ("electronics", "macbook air m3 512"),
]


PROBE_URLS: list[tuple[str, str, str]] = [
    (
        "tyres",
        "шины 205/55 R16 зимние шипованные",
        "https://koleso.ru/catalog/tyres/amtel/nordmaster-evo-11562/"
        "amtel-nordmaster-evo-205-55r16-94t----shipovannaya/",
    ),
    (
        "tyres",
        "шины 205/55 R16 зимние шипованные",
        "https://www.shinservice.ru/catalog/tyres/diameter-is-16/pins-is-1/profile-is-55/"
        "season-is-winter/width-is-205/",
    ),
    ("tyres", "шины 225/45 R17 летние", "https://koleso.ru/catalog/tyres/all_sizes/225-45-17/leto/"),
    ("office", "картридж HP CE285A", "https://www.kns.ru/product/kartridzh-hp-ce285a/"),
    (
        "office",
        "МФУ HP LaserJet Pro M4103fdw",
        "https://www.kns.ru/product/mfu-hp-laserjet-pro-mfp-m4103fdw-2z629a/",
    ),
    ("office", "картридж Canon 039H", "https://global-cartridge.ru/canon-cartridge-039h"),
    ("apparel", "кеды цена", "https://groupprice.ru/products/36756732-kedy-bosonogie"),
    ("apparel", "кроссовки женские", "https://respect-shoes.ru/k63_075980_r/"),
    (
        "apparel",
        "кроссовки nike",
        "https://sneakerhead.ru/shoes/sneakers/air-force-1-le-gs-dh2920-111/",
    ),
]

PRICE_RE = re.compile(r"(?:от\s*)?\d[\d\s\xa0]{2,}\s*(?:₽|руб\.?|р\.)", re.I)
IMG_RE = re.compile(r"https?://[^\"\s<>]+\.(?:jpg|jpeg|png|webp)[^\"\s<>]*", re.I)
SPEC_MARKERS = (
    "Характеристики",
    "characteristics",
    "specifications",
    "params",
    "Состав",
    "Материал",
    "Размер",
    "Диаметр",
    "Сезон",
    "Артикул",
)


async def _image_ok(url: Any) -> bool:
    if not url:
        return False
    try:
        async with httpx.AsyncClient(
            timeout=4,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            resp = await client.get(str(url))
    except Exception:
        return False
    return resp.status_code < 400 and (resp.headers.get("content-type") or "").startswith("image/")


async def run_current_scraper(limit: int) -> list[dict[str, Any]]:
    scraper = RunetScraper(timeout_s=5.0, max_urls=18)
    rows: list[dict[str, Any]] = []
    for category, query in RUNET_QUERIES:
        started = time.perf_counter()
        result = await scraper.search(NormalizedQuery(raw=query, normalized=query), limit=limit)
        took_ms = int((time.perf_counter() - started) * 1000)
        image_checks = [await _image_ok(offer.image) for offer in result.offers]
        row = {
            "category": category,
            "query": query,
            "count": len(result.offers),
            "image_fields": sum(1 for offer in result.offers if offer.image),
            "image_ok": sum(image_checks),
            "characteristics_counts": [len(offer.characteristics) for offer in result.offers],
            "error": result.error,
            "took_ms": took_ms,
            "offers": [
                {
                    "name": offer.name,
                    "price": str(offer.price),
                    "image": str(offer.image) if offer.image else None,
                    "image_ok": ok,
                    "characteristics_count": len(offer.characteristics),
                    "url": str(offer.url),
                }
                for offer, ok in zip(result.offers, image_checks, strict=False)
            ],
        }
        rows.append(row)
        print(
            f"CURRENT {category:<11} count={row['count']} img_ok={row['image_ok']} "
            f"err={row['error']} ms={took_ms} :: {query}"
        )
    return rows


async def probe_researched_urls() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async with AsyncSession(impersonate="chrome131", timeout=15) as session:
        for category, query, url in PROBE_URLS:
            row: dict[str, Any] = {"category": category, "query": query, "url": url}
            try:
                resp = await session.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU,ru;q=0.9"},
                )
                page = resp.text if isinstance(resp.text, str) else ""
            except Exception as exc:
                row.update({"error": repr(exc)})
                rows.append(row)
                print(f"PROBE   {category:<11} ERROR {exc!r} :: {url}")
                continue

            visible = html_lib.unescape(re.sub(r"<[^>]+>", " ", page))
            products = [payload for payload in _walk_jsonld(page) if _is_product(payload)]
            current_offers = [
                offer
                for payload in products
                if (offer := _to_offer(url, payload, query_tokens=_tokenize(query))) is not None
            ]
            row.update({
                "status_code": resp.status_code,
                "html_len": len(page),
                "jsonld_products": len(products),
                "current_offers": len(current_offers),
                "microdata_product": "schema.org/Product" in page or ("itemscope" in page and "Product" in page),
                "next_data": "__NEXT_DATA__" in page,
                "nuxt_data": "__NUXT" in page,
                "og_image": "og:image" in page,
                "price_regex_count": len(PRICE_RE.findall(visible[:500_000])),
                "image_regex_count": len(IMG_RE.findall(page[:500_000])),
                "spec_markers": [marker for marker in SPEC_MARKERS if marker in page or marker in visible],
                "current_offer_samples": [
                    {
                        "name": offer.name,
                        "price": str(offer.price),
                        "image": str(offer.image) if offer.image else None,
                        "characteristics_count": len(offer.characteristics),
                    }
                    for offer in current_offers[:3]
                ],
                "generic_price_samples": PRICE_RE.findall(visible[:500_000])[:5],
                "generic_image_samples": IMG_RE.findall(page[:500_000])[:3],
            })
            rows.append(row)
            print(
                f"PROBE   {category:<11} status={resp.status_code} jsonld={len(products)} "
                f"current={len(current_offers)} prices={row['price_regex_count']} "
                f"images={row['image_regex_count']} specs={len(row['spec_markers'])} :: {query}"
            )
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("all", "current", "probes"), default="all")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--out", default="scripts/runet_websearch_diagnostics.json")
    args = parser.parse_args()

    payload: dict[str, Any] = {}
    if args.mode in ("all", "current"):
        payload["current_scraper"] = await run_current_scraper(args.limit)
    if args.mode in ("all", "probes"):
        payload["researched_url_probes"] = await probe_researched_urls()

    out_path = Path(args.out)
    await asyncio.to_thread(_write_json, out_path, payload)
    print(f"\nFull diagnostics: {out_path}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
