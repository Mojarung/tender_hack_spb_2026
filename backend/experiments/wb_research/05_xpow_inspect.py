"""Inspect WB Proof-of-Work header (x-pow) over a series of plain requests.

Цель: понять механику `x-pow: status=...;challenge=...` — переходит ли он
сам в `valid` после серии запросов, видим ли мы set-cookie от WBAAS,
меняется ли `challenge` детерминированно. Это разведка без обхода: мы
просто фиксируем, что отдаёт WB при медленной серии plain-запросов с одной
shared cookie jar.

Безопасно: один client, ~10 RPM, остановка на 429.
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

N_REQUESTS = 8
PAUSE_S = 6.0


async def main() -> int:
    import httpx

    query = query_from_argv()
    print(f"[i] WB x-pow inspect query={query!r} N={N_REQUESTS} pause={PAUSE_S}s")

    transitions: list[dict] = []
    async with httpx.AsyncClient(http2=True, headers=PLAIN_HEADERS, timeout=10) as client:
        for i in range(N_REQUESTS):
            with Timer() as t:
                try:
                    resp = await client.get(SEARCH_URL, params=params(query, page=1))
                except httpx.HTTPError as exc:
                    transitions.append({"i": i, "error": str(exc)})
                    print(f"[!] {i}: {exc}")
                    break

            x_pow = resp.headers.get("x-pow")
            set_cookie = resp.headers.get("set-cookie")
            cookie_names = [c.name for c in client.cookies.jar]
            entry = {
                "i": i,
                "status": resp.status_code,
                "ms": t.elapsed_ms,
                "x_pow": x_pow,
                "set_cookie": set_cookie,
                "cookie_jar": cookie_names,
                "retry_after": resp.headers.get("retry-after"),
                "products": (
                    len(resp.json().get("products") or [])
                    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json")
                    else None
                ),
            }
            transitions.append(entry)
            print(
                f"[i] {i}: status={resp.status_code} x-pow={x_pow!r} "
                f"jar={cookie_names} products={entry['products']}"
            )
            if resp.status_code == 429:
                print(f"[!] 429 — stop. retry-after={resp.headers.get('retry-after')}")
                break
            await asyncio.sleep(PAUSE_S)

    save_json("05_xpow_inspect", {"query": query, "transitions": transitions})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
