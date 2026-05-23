"""07 — 429 behaviour probe (don't run repeatedly — this DOES trigger blocks).

PURPOSE
    Find out what WB actually does at the rate-limit boundary:
      - threshold (RPS where 429 starts)
      - cooldown length (does Retry-After / X-Ratelimit-Retry exist?)
      - per-shard vs per-IP scope
      - whether HTTP/2 vs HTTP/1.1 matters

    OUTPUTS a table of (request#, status, elapsed_ms, headers). When
    the first 429 lands, we sleep advertised cooldown + 5 s and try
    one more request to confirm we're unblocked.

USAGE
    cd wb_research
    uv run python 07_429_handling.py [rps=15] [duration_s=20]

WARNING
    This sends ~rps*duration_s requests in a tight loop. Don't loop
    on the same query in prod — keep it offline.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    WB_DEFAULT_DEST,
    WB_HEADERS,
    WB_REGIONS,
    err,
    info,
    ok,
    save_json,
    section,
    warn,
)

SEARCH = "https://search.wb.ru/exactmatch/ru/common/v18/search"


def _params() -> dict[str, str]:
    return {
        "ab_testid": "false", "appType": "1", "curr": "rub",
        "dest": str(WB_DEFAULT_DEST), "hide_dtype": "13", "lang": "ru",
        "page": "1", "query": "ноутбук", "regions": WB_REGIONS,
        "resultset": "catalog", "sort": "popular", "spp": "30",
        "suppressSpellcheck": "false",
    }


async def main() -> int:
    section("WB 429 PROBE — find rate-limit ceiling (offline)")

    try:
        import httpx
    except ImportError:
        err("httpx not installed")
        return 3

    rps = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    duration_s = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    warn(f"about to send ~{rps * duration_s} requests at {rps} RPS — proceed in 3 s")
    await asyncio.sleep(3)

    rows: list[dict] = []
    first_429_at: float | None = None
    interval = 1.0 / rps
    t0 = time.perf_counter()

    async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=8) as c:
        for n in range(rps * duration_s):
            ts = time.perf_counter() - t0
            req_start = time.perf_counter()
            try:
                r = await c.get(SEARCH, params=_params())
                status = r.status_code
                # capture only the rate-limit-relevant headers
                rl_headers = {
                    k: v for k, v in r.headers.items()
                    if k.lower().startswith(("retry-after", "x-ratelimit"))
                }
                el = int((time.perf_counter() - req_start) * 1000)
            except httpx.HTTPError as exc:
                status = 0
                rl_headers = {"network_error": str(exc)}
                el = int((time.perf_counter() - req_start) * 1000)

            rows.append({
                "n": n, "t_s": round(ts, 2),
                "status": status, "elapsed_ms": el, "headers": rl_headers,
            })
            marker = " " if status == 200 else ("!" if status == 429 else "x")
            print(f"  {marker} [{n:03d}] t={ts:5.2f}s  HTTP {status:>3}  {el:>4}ms  {rl_headers or '-'}")

            if status == 429 and first_429_at is None:
                first_429_at = ts
                # Try to honour Retry-After / X-Ratelimit-Retry, otherwise wait 90s
                wait = (
                    int(rl_headers.get("x-ratelimit-retry") or 0)
                    or int(rl_headers.get("retry-after") or 0)
                    or 90
                )
                warn(f"first 429 at t={ts:.2f}s. waiting {wait}s + 5s before one more probe.")
                await asyncio.sleep(wait + 5)
                continue

            await asyncio.sleep(interval)

    section("Summary")
    n_200 = sum(1 for r in rows if r["status"] == 200)
    n_429 = sum(1 for r in rows if r["status"] == 429)
    n_5xx = sum(1 for r in rows if r["status"] >= 500)
    info(f"total = {len(rows)}, 200 = {n_200}, 429 = {n_429}, 5xx = {n_5xx}")
    if first_429_at:
        warn(f"FIRST 429 at t={first_429_at:.2f}s — that's the threshold for {rps} RPS on this IP")
    else:
        ok(f"no 429 in {duration_s}s at {rps} RPS — your IP is safe at this rate")

    path = save_json("07_429_probe", {"rps": rps, "duration_s": duration_s, "rows": rows})
    ok(f"saved → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
