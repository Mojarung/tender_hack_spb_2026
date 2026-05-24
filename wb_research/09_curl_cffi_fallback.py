"""09 — curl_cffi (chrome impersonate) as a plan-B for httpx blocks.

PURPOSE
    WB doesn't currently check JA3, so plain httpx works (see 01).
    But if WB ever turns it on, curl_cffi with `impersonate="chrome"`
    sends the actual Chrome TLS ClientHello — much harder to fingerprint
    out. This script runs the same search via both clients and reports:

      - Did either return 403 where the other returned 200?
      - Latency difference?
      - Response shape / cookies set?

    A/B comparison helps decide whether to keep curl_cffi as a true
    fallback or drop it entirely from WB code paths.

USAGE
    cd wb_research
    uv run python 09_curl_cffi_fallback.py "шины зимние"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    WB_DEFAULT_DEST,
    WB_HEADERS,
    WB_REGIONS,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
)

SEARCH = "https://search.wb.ru/exactmatch/ru/common/v18/search"


def _params(query: str) -> dict[str, str]:
    return {
        "ab_testid": "false", "appType": "1", "curr": "rub",
        "dest": str(WB_DEFAULT_DEST), "hide_dtype": "13", "lang": "ru",
        "page": "1", "query": query, "regions": WB_REGIONS,
        "resultset": "catalog", "sort": "popular", "spp": "30",
        "suppressSpellcheck": "false",
    }


async def _hit_httpx(query: str) -> dict:
    import httpx

    with Timer() as t:
        try:
            async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=10) as c:
                r = await c.get(SEARCH, params=_params(query))
                products = (
                    r.json().get("products") or
                    (r.json().get("data") or {}).get("products") or []
                ) if r.status_code == 200 else []
                return {
                    "client": "httpx", "status": r.status_code,
                    "elapsed_ms": t.elapsed_ms, "products": len(products),
                    "cookies": dict(r.cookies),
                    "server": r.headers.get("server", "-"),
                }
        except Exception as exc:
            return {"client": "httpx", "error": str(exc), "elapsed_ms": t.elapsed_ms}


async def _hit_curl_cffi(query: str, impersonate: str) -> dict:
    from curl_cffi.requests import AsyncSession

    with Timer() as t:
        try:
            async with AsyncSession(impersonate=impersonate, timeout=10) as s:
                r = await s.get(SEARCH, params=_params(query), headers=WB_HEADERS)
                try:
                    products = (
                        r.json().get("products") or
                        (r.json().get("data") or {}).get("products") or []
                    ) if r.status_code == 200 else []
                except Exception:
                    products = []
                return {
                    "client": f"curl_cffi/{impersonate}", "status": r.status_code,
                    "elapsed_ms": t.elapsed_ms, "products": len(products),
                    "cookies": dict(r.cookies),
                    "server": r.headers.get("server", "-"),
                }
        except Exception as exc:
            return {"client": f"curl_cffi/{impersonate}", "error": str(exc),
                    "elapsed_ms": t.elapsed_ms}


async def main() -> int:
    section("WB A/B — httpx vs curl_cffi (chrome impersonate)")

    query = query_from_argv()
    info(f"query = {query!r}")

    rows = [
        await _hit_httpx(query),
        await _hit_curl_cffi(query, "chrome"),
        await _hit_curl_cffi(query, "chrome131"),
        await _hit_curl_cffi(query, "safari17_2_ios"),
    ]
    print()
    print(f"  {'client':<28}{'status':>8}{'elapsed':>10}{'products':>10}  server")
    print(f"  {'-'*28}{'-'*8}{'-'*10}{'-'*10}  {'-'*16}")
    for r in rows:
        client = r["client"]
        status = r.get("status", "ERR")
        el = r.get("elapsed_ms", 0)
        n = r.get("products", "-")
        srv = r.get("server", "-")
        print(f"  {client:<28}{status:>8}{el:>9}ms{n:>10}  {srv}")

    section("Conclusion")
    httpx_ok = rows[0].get("status") == 200 and rows[0].get("products", 0) > 0
    cf_ok = any(r.get("status") == 200 and r.get("products", 0) > 0 for r in rows[1:])
    if httpx_ok and cf_ok:
        ok("BOTH work — keep plain httpx in prod (faster, fewer deps)")
    elif httpx_ok:
        ok("httpx works, curl_cffi failing — stick with httpx")
    elif cf_ok:
        err("httpx blocked but curl_cffi works — WB started JA3 checking, switch to curl_cffi")
    else:
        err("NEITHER works — likely 429 or IP block, see _out/ JSON for headers")

    save_json("09_curl_cffi_ab", {"query": query, "rows": rows})
    return 0 if (httpx_ok or cf_ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
