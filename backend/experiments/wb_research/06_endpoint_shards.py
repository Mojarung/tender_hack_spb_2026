"""Probe alternative WB search hosts/endpoints.

WB фронт обращается к нескольким публичным host-ам:
- search.wb.ru   — основной exactmatch (наш prod)
- u-search.wb.ru — пользовательский шард (часто другой WAF-бюджет)
- search-by-regions.wb.ru — региональный шард
- suggestions.wildberries.ru — autocomplete (другой rate-budget, может
  использоваться как лёгкий health-check / прогрев)
- catalog.wb.ru — каталожные подборки (не текстовый search, но важно
  знать поведение)

Гипотеза: если основной шард залочен, остальные шарды могут отдавать
валидный JSON. Это не «обход», а корректный fallback на публичный
endpoint того же сервиса.

Безопасно: по 1 запросу на каждый host, plain headers, последовательно.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import Timer, params, query_from_argv, save_json

PLAIN_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

ENDPOINTS = [
    ("search.wb.ru", "https://search.wb.ru/exactmatch/ru/common/v18/search"),
    ("u-search.wb.ru", "https://u-search.wb.ru/exactmatch/ru/common/v18/search"),
    ("search-by-regions", "https://search-by-regions.wb.ru/exactmatch/ru/common/v18/search"),
    # v17 fallback (старая версия может иметь другой бюджет/PoW политику)
    ("search.wb.ru/v17", "https://search.wb.ru/exactmatch/ru/common/v17/search"),
    # Suggestions — отдельный сервис, не выдаёт products, но виден ли rate-budget
    ("suggestions", "https://suggestions.wildberries.ru/api/v8/hint"),
]


async def main() -> int:
    import httpx

    query = query_from_argv()
    print(f"[i] WB endpoint sweep query={query!r}")

    rows: list[dict] = []
    async with httpx.AsyncClient(http2=True, headers=PLAIN_HEADERS, timeout=10) as client:
        for name, url in ENDPOINTS:
            if "suggestions" in url:
                req_params = {"query": query, "gender": "common", "locale": "ru", "lang": "ru", "appType": "1"}
            else:
                req_params = params(query)
            with Timer() as t:
                try:
                    resp = await client.get(url, params=req_params)
                    status = resp.status_code
                    x_pow = resp.headers.get("x-pow")
                    n_products: int | None = None
                    if status == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                        body = resp.json()
                        if isinstance(body, dict):
                            n_products = len(
                                body.get("products")
                                or (body.get("data") or {}).get("products")
                                or []
                            )
                        elif isinstance(body, list):
                            n_products = len(body)
                    err = None
                except httpx.HTTPError as exc:
                    status = -1
                    x_pow = None
                    n_products = None
                    err = str(exc)
            rows.append(
                {
                    "name": name,
                    "url": url,
                    "status": status,
                    "ms": t.elapsed_ms,
                    "x_pow": x_pow,
                    "products": n_products,
                    "error": err,
                }
            )
            print(f"[i] {name:25} status={status} ms={t.elapsed_ms} x-pow={x_pow!r} products={n_products}")
            await asyncio.sleep(2.0)

    save_json("06_endpoint_shards", {"query": query, "rows": rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
