"""appType variants — у WB разные клиенты ходят с разным `appType`.

Известные значения:
- 1   — desktop web (наш текущий prod)
- 64  — мобильный web
- 128 — мобильное приложение

Гипотеза: разные appType могут идти через разные WAF-правила и иметь
независимые rate-budget. Это публичный параметр, не подмена идентичности —
просто другой канал того же API.

Безопасно: по 1 запросу на каждый appType, паузы между ними.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import SEARCH_URL, Timer, params, query_from_argv, save_json

PLAIN_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

APP_TYPES = ["1", "64", "128"]


async def main() -> int:
    import httpx

    query = query_from_argv()
    print(f"[i] WB appType sweep query={query!r}")

    rows: list[dict] = []
    async with httpx.AsyncClient(http2=True, headers=PLAIN_HEADERS, timeout=10) as client:
        for app_type in APP_TYPES:
            p = params(query)
            p["appType"] = app_type
            with Timer() as t:
                try:
                    resp = await client.get(SEARCH_URL, params=p)
                    status = resp.status_code
                    x_pow = resp.headers.get("x-pow")
                    n = (
                        len(resp.json().get("products") or [])
                        if status == 200 and resp.headers.get("content-type", "").startswith("application/json")
                        else None
                    )
                    err = None
                except httpx.HTTPError as exc:
                    status = -1
                    x_pow = None
                    n = None
                    err = str(exc)
            rows.append(
                {
                    "appType": app_type,
                    "status": status,
                    "ms": t.elapsed_ms,
                    "x_pow": x_pow,
                    "products": n,
                    "error": err,
                }
            )
            print(f"[i] appType={app_type:>3}: status={status} ms={t.elapsed_ms} x-pow={x_pow!r} products={n}")
            await asyncio.sleep(8.0)

    save_json("10_apptype_variants", {"query": query, "rows": rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
