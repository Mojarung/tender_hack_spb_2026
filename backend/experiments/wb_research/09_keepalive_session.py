"""Keepalive vs new client per request — влияет ли connection reuse на 429.

Гипотеза: WBAAS считает request rate по (IP, TLS session). Если каждый
запрос делает новый TLS handshake, мы выглядим как «много новых клиентов»
и можем быстрее упереться в лимит. Один долгоживущий keepalive client —
это «один клиент, мирно листающий сайт».

В production scrapers/wb.py создаёт `httpx.AsyncClient` внутри `_fetch`,
то есть **новый client на каждый запрос**. Если keepalive статистически
устойчивее — это P0 квик-вин: вынести client в self.

Безопасно: 6 запросов за 60с (1 RPM × 6 ≈ 6 RPM, ниже первого порога).
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

N = 6
PAUSE_S = 10.0


async def run_new_client_each(query: str) -> list[dict]:
    import httpx

    out: list[dict] = []
    for i in range(N):
        with Timer() as t:
            try:
                async with httpx.AsyncClient(http2=True, headers=PLAIN_HEADERS, timeout=10) as client:
                    resp = await client.get(SEARCH_URL, params=params(query))
                status = resp.status_code
                x_pow = resp.headers.get("x-pow")
                err = None
            except httpx.HTTPError as exc:
                status = -1
                x_pow = None
                err = str(exc)
        out.append({"i": i, "status": status, "ms": t.elapsed_ms, "x_pow": x_pow, "error": err})
        print(f"[new] {i}: status={status} ms={t.elapsed_ms} x-pow={x_pow!r}")
        await asyncio.sleep(PAUSE_S)
    return out


async def run_keepalive(query: str) -> list[dict]:
    import httpx

    out: list[dict] = []
    async with httpx.AsyncClient(http2=True, headers=PLAIN_HEADERS, timeout=10) as client:
        for i in range(N):
            with Timer() as t:
                try:
                    resp = await client.get(SEARCH_URL, params=params(query))
                    status = resp.status_code
                    x_pow = resp.headers.get("x-pow")
                    err = None
                except httpx.HTTPError as exc:
                    status = -1
                    x_pow = None
                    err = str(exc)
            out.append({"i": i, "status": status, "ms": t.elapsed_ms, "x_pow": x_pow, "error": err})
            print(f"[keep] {i}: status={status} ms={t.elapsed_ms} x-pow={x_pow!r}")
            await asyncio.sleep(PAUSE_S)
    return out


async def main() -> int:
    query = query_from_argv()
    print(f"[i] keepalive vs new-client query={query!r} N={N} pause={PAUSE_S}s")

    print("[i] Phase 1: new client per request")
    new_client = await run_new_client_each(query)

    print("[i] cooldown 30s before phase 2")
    await asyncio.sleep(30.0)

    print("[i] Phase 2: shared keepalive client")
    keepalive = await run_keepalive(query)

    save_json(
        "09_keepalive_session",
        {
            "query": query,
            "new_client_per_request": new_client,
            "keepalive": keepalive,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
