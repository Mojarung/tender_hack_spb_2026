from __future__ import annotations

import asyncio
import importlib
import random
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parent))

from _common import SEARCH_URL, Timer, params, query_from_argv, save_json

PLAIN_HEADERS = importlib.import_module("03_plain_request").PLAIN_HEADERS

STOP_STATUSES = {403, 429}
DEFAULT_RPM_STEPS = [6, 10, 15, 20, 30]
REQUESTS_PER_STEP = 5


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


async def _request(client: object, query: str, index: int) -> dict[str, object]:
    with Timer() as timer:
        resp = await client.get(SEARCH_URL, params=params(query, page=1))
    products = []
    if resp.status_code == 200:
        try:
            body = resp.json()
            products = body.get("products") or (body.get("data") or {}).get("products") or []
        except ValueError:
            products = []
    return {
        "index": index,
        "status": resp.status_code,
        "elapsed_ms": timer.elapsed_ms,
        "bytes": len(resp.content),
        "products": len(products),
        "retry_after": resp.headers.get("retry-after"),
        "x_pow": resp.headers.get("x-pow"),
    }


async def _probe_step(client: object, query: str, rpm: int) -> dict[str, object]:
    interval_s = 60 / rpm
    results = []
    print(f"[i] probing rpm={rpm}, interval≈{interval_s:.2f}s")
    for index in range(REQUESTS_PER_STEP):
        result = await _request(client, query, index=index + 1)
        results.append(result)
        print(
            "[i] "
            f"rpm={rpm} req={index + 1}/{REQUESTS_PER_STEP} "
            f"status={result['status']} time={result['elapsed_ms']}ms "
            f"products={result['products']} retry-after={result['retry_after']}"
        )
        if result["status"] in STOP_STATUSES:
            retry_after = _retry_after_seconds(result.get("retry_after"))
            cooldown_s = retry_after if retry_after is not None else 120.0
            print(f"[!] stop status={result['status']}; cooldown recommendation={cooldown_s:.1f}s")
            break
        if index < REQUESTS_PER_STEP - 1:
            jitter = random.uniform(0.15, 0.35) * interval_s
            await asyncio.sleep(interval_s + jitter)
    ok_results = [item for item in results if item["status"] == 200]
    latencies = [int(item["elapsed_ms"]) for item in ok_results]
    return {
        "rpm": rpm,
        "requested": len(results),
        "ok": len(ok_results),
        "blocked": any(item["status"] in STOP_STATUSES for item in results),
        "avg_elapsed_ms": int(mean(latencies)) if latencies else None,
        "results": results,
    }


async def main() -> int:
    import httpx

    query = query_from_argv()
    print(f"[i] WB safe rate probe query={query!r}")
    print("[i] mode=plain headers, sequential requests, stop on 403/429")
    report = {"query": query, "steps": []}
    async with httpx.AsyncClient(http2=True, headers=PLAIN_HEADERS, timeout=10) as client:
        for rpm in DEFAULT_RPM_STEPS:
            step = await _probe_step(client, query, rpm)
            report["steps"].append(step)
            if step["blocked"]:
                break
            await asyncio.sleep(10)
    path = save_json("04_safe_rate_probe", report)
    print(f"[+] saved={path}")
    blocked = any(step["blocked"] for step in report["steps"])
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
