"""Cookie warm-up: посетить главную WB, забрать cookies, и только потом
ходить в search.wb.ru с общей jar.

Идея: WBAAS/PoW обычно даёт `x-pow: status=valid` сессиям, у которых уже
есть базовые cookies от www.wildberries.ru (`__wbl`, `BasketUUID`, региональные
куки). Мы не решаем challenge сами — мы просто эмулируем нормальный поток
браузера, который сначала открыл сайт, а потом сделал XHR.

Безопасно: один visit главной (HTML), потом 3 запроса к search с паузой 6с.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import SEARCH_URL, Timer, params, query_from_argv, save_json

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

HOMEPAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# После warmup — plain shape, который уже работал в эксперименте 03
SEARCH_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


async def main() -> int:
    import httpx

    query = query_from_argv()
    print(f"[i] WB cookie warm-up query={query!r}")

    log: list[dict] = []
    async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=15) as client:
        # 1) homepage
        with Timer() as t:
            try:
                resp = await client.get("https://www.wildberries.ru/", headers=HOMEPAGE_HEADERS)
                home_status = resp.status_code
                home_err = None
            except httpx.HTTPError as exc:
                home_status = -1
                home_err = str(exc)
        cookies_after_home = [c.name for c in client.cookies.jar]
        print(f"[i] homepage: status={home_status} ms={t.elapsed_ms} cookies={cookies_after_home}")
        log.append(
            {
                "step": "homepage",
                "status": home_status,
                "ms": t.elapsed_ms,
                "cookies": cookies_after_home,
                "error": home_err,
            }
        )

        await asyncio.sleep(2.0)

        # 2) три search-запроса с общей jar
        for i in range(3):
            with Timer() as t:
                try:
                    resp = await client.get(SEARCH_URL, params=params(query), headers=SEARCH_HEADERS)
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
            print(f"[i] search[{i}]: status={status} ms={t.elapsed_ms} x-pow={x_pow!r} products={n}")
            log.append(
                {
                    "step": f"search_{i}",
                    "status": status,
                    "ms": t.elapsed_ms,
                    "x_pow": x_pow,
                    "products": n,
                    "cookies": [c.name for c in client.cookies.jar],
                    "error": err,
                }
            )
            if status == 429:
                break
            await asyncio.sleep(6.0)

    save_json("07_cookie_warmup", {"query": query, "log": log})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
