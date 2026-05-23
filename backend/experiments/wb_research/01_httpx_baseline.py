from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import HEADERS, SEARCH_URL, Timer, params, query_from_argv, save_json


async def main() -> int:
    import httpx

    query = query_from_argv()
    print(f"[i] WB httpx baseline query={query!r}")
    with Timer() as timer:
        async with httpx.AsyncClient(http2=True, headers=HEADERS, timeout=10) as client:
            resp = await client.get(SEARCH_URL, params=params(query))

    print(f"[i] status={resp.status_code} time={timer.elapsed_ms}ms bytes={len(resp.content)}")
    if resp.status_code != 200:
        save_json("01_httpx_block", {"status": resp.status_code, "body": resp.text[:2000]})
        return 1

    body = resp.json()
    products = body.get("products") or (body.get("data") or {}).get("products") or []
    print(f"[+] products={len(products)}")
    save_json("01_httpx_ok", {"count": len(products), "sample": products[:5]})
    return 0 if products else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
