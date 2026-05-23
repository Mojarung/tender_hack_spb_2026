"""12 — WB warm cookies → curl_cffi (the fast HTTP-only path).

PURPOSE
    After 11_browser_warmer.py has done the expensive nodriver dance
    once, this script does the cheap HTTP version. Loads cookies from
    `_out/wb_cookies.json` and tries the search endpoint with:
      A) plain httpx + cookies
      B) curl_cffi (chrome impersonate) + cookies

    Whichever returns HTTP 200 wins — that's the production path.

    If BOTH fail with 429/PG-41, WB's PoW token must be per-request
    (not cookie-cached) and we need to either re-run the browser
    fetch every query OR keep the browser tab alive and do
    same-origin fetches forever (see 13_pow_inspector.py for what to
    look at).

USAGE
    cd wb_research
    # Run 11 once first to plant cookies, then:
    uv run python 12_warm_cookies_to_curl.py "ноутбук"
    uv run python 12_warm_cookies_to_curl.py "шины 205 55 R16"
    uv run python 12_warm_cookies_to_curl.py "iphone 15"

EXIT CODES
    0 — at least one HTTP client got 200 with products
    1 — both blocked / 429
    2 — 200 but products[] empty
    3 — cookies file missing
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    OUT_DIR,
    WB_DEFAULT_DEST,
    WB_HEADERS,
    WB_REGIONS,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)


def _elapsed_ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)

SEARCH = "https://search.wb.ru/exactmatch/ru/common/v18/search"
COOKIES_FILE = OUT_DIR / "wb_cookies.json"


def _params(query: str) -> dict[str, str]:
    return {
        "ab_testid": "false", "appType": "1", "curr": "rub",
        "dest": str(WB_DEFAULT_DEST), "hide_dtype": "13", "lang": "ru",
        "page": "1", "query": query, "regions": WB_REGIONS,
        "resultset": "catalog", "sort": "popular", "spp": "30",
        "suppressSpellcheck": "false",
    }


def _load_cookies() -> list[dict]:
    if not COOKIES_FILE.exists():
        return []
    try:
        data = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


async def _hit_httpx(query: str, cookies: list[dict]) -> dict:
    import httpx

    jar = httpx.Cookies()
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain")
        if not (name and value is not None):
            continue
        try:
            jar.set(name, value, domain=domain or ".wildberries.ru", path=c.get("path") or "/")
        except Exception:
            continue

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            http2=True, headers=WB_HEADERS, cookies=jar, timeout=12,
        ) as c:
            r = await c.get(SEARCH, params=_params(query))
            status = r.status_code
            products = []
            if status == 200:
                try:
                    body = r.json()
                    products = (
                        body.get("products")
                        or (body.get("data") or {}).get("products")
                        or []
                    )
                except Exception:
                    pass
            return {
                "client": "httpx",
                "status": status,
                "elapsed_ms": _elapsed_ms_since(t0),
                "products": len(products),
                "status_no_id": r.headers.get("status-no-id", "-"),
                "body_preview": "" if status == 200 else r.text[:300],
                "first_three": [
                    {"nm": p.get("id"), "name": (p.get("name") or "")[:60]}
                    for p in products[:3]
                ],
            }
    except Exception as exc:
        return {"client": "httpx", "error": str(exc), "elapsed_ms": _elapsed_ms_since(t0)}


async def _hit_curl_cffi(query: str, cookies: list[dict], impersonate: str) -> dict:
    from curl_cffi.requests import AsyncSession

    t0 = time.perf_counter()
    try:
        async with AsyncSession(impersonate=impersonate, timeout=12) as s:
            for c in cookies:
                name = c.get("name")
                value = c.get("value")
                if not (name and value is not None):
                    continue
                try:
                    s.cookies.set(
                        name, value,
                        domain=c.get("domain") or ".wildberries.ru",
                        path=c.get("path") or "/",
                    )
                except Exception:
                    continue
            r = await s.get(SEARCH, params=_params(query), headers=WB_HEADERS)
            status = r.status_code
            products = []
            if status == 200:
                try:
                    body = r.json()
                    products = (
                        body.get("products")
                        or (body.get("data") or {}).get("products")
                        or []
                    )
                except Exception:
                    pass
            return {
                "client": f"curl_cffi/{impersonate}",
                "status": status,
                "elapsed_ms": _elapsed_ms_since(t0),
                "products": len(products),
                "status_no_id": r.headers.get("status-no-id", "-"),
                "body_preview": "" if status == 200 else r.text[:300],
                "first_three": [
                    {"nm": p.get("id"), "name": (p.get("name") or "")[:60]}
                    for p in products[:3]
                ],
            }
    except Exception as exc:
        return {"client": f"curl_cffi/{impersonate}", "error": str(exc),
                "elapsed_ms": _elapsed_ms_since(t0)}


async def main() -> int:
    section("WB WARM-COOKIES → curl_cffi/httpx (HTTP-only fast path)")

    cookies = _load_cookies()
    if not cookies:
        err(f"no cookies at {COOKIES_FILE}")
        err("→ run 11_browser_warmer.py first to plant them")
        return 3

    interesting = sorted({
        c["name"] for c in cookies
        if c.get("name") and any(
            tok in c["name"].lower()
            for tok in ("wbl", "wbauid", "basket", "pow", "wbx", "wba")
        )
    })
    ok(f"loaded {len(cookies)} cookies; load-bearing names: {interesting or '(none)'}")

    query = query_from_argv("ноутбук")
    info(f"query = {query!r}\n")

    rows = [
        await _hit_httpx(query, cookies),
        await _hit_curl_cffi(query, cookies, "chrome"),
        await _hit_curl_cffi(query, cookies, "chrome131"),
    ]

    print()
    print(f"  {'client':<28}{'status':>8}{'elapsed':>10}{'products':>10}  status-no-id")
    print(f"  {'-'*28}{'-'*8}{'-'*10}{'-'*10}  {'-'*16}")
    for r in rows:
        client = r["client"]
        status = r.get("status", "ERR")
        el = r.get("elapsed_ms", 0)
        n = r.get("products", "-")
        sni = r.get("status_no_id", "-")
        print(f"  {client:<28}{status:>8}{el:>9}ms{n:>10}  {sni}")

    print()
    winner = next((r for r in rows if r.get("status") == 200 and r.get("products", 0) > 0), None)
    section("Verdict")
    if winner:
        ok(f"WIN: {winner['client']} returned 200 + {winner['products']} products")
        for p in winner["first_three"]:
            info(f"  • nm={p['nm']} {p['name']}")
        save_json("12_warm_curl_ok", {"query": query, "winner": winner, "all": rows})
        info("→ wire this path into prod scrapers/wb.py")
        return 0
    err("ALL clients blocked even WITH warmed cookies. PoW token must be per-request.")
    err("Options:")
    err("  • re-run 11_browser_warmer.py before every query (slow but works)")
    err("  • keep a long-running browser session and do same-origin fetches forever")
    err("  • run 13_pow_inspector.py to see what x-pow / cookie WB actually sets per request")
    save_json("12_warm_curl_block", {"query": query, "rows": rows})
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
