"""10 — DIAGNOSE: am I rate-limited / blocked / what's broken?

PURPOSE
    One-shot health probe of the whole WB surface. Runs each endpoint
    once and reports OK/blocked. Use when prod search returns 0 — this
    tells you which layer is the culprit before diving in.

USAGE
    cd wb_research
    uv run python 10_diagnose.py
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
    basket_for,
    card_json_url,
    err,
    feedbacks_host,
    info,
    ok,
    save_json,
    section,
    warn,
)

SEARCH = "https://search.wb.ru/exactmatch/ru/common/v18/search"


async def main() -> int:
    section("WB DIAGNOSE — health probe of search / card / feedbacks")

    try:
        import httpx
    except ImportError:
        err("httpx not installed")
        return 3

    rows: list[dict] = []

    async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=10) as c:

        # 1) search baseline — "ноутбук"
        with Timer() as t:
            r = await c.get(SEARCH, params={
                "ab_testid": "false", "appType": "1", "curr": "rub",
                "dest": str(WB_DEFAULT_DEST), "hide_dtype": "13", "lang": "ru",
                "page": "1", "query": "ноутбук", "regions": WB_REGIONS,
                "resultset": "catalog", "sort": "popular", "spp": "30",
                "suppressSpellcheck": "false",
            })
        first_nm = None
        first_root = None
        if r.status_code == 200:
            try:
                products = r.json().get("products") or []
                if products:
                    first_nm = products[0].get("id")
                    first_root = products[0].get("root")
            except Exception:
                pass
        rows.append({
            "step": "search.wb.ru/v18", "status": r.status_code,
            "elapsed_ms": t.elapsed_ms, "first_nm": first_nm, "first_root": first_root,
        })
        _print_row(rows[-1])

        # 2) card.json on the first nm
        if first_nm:
            with Timer() as t2:
                r2 = await c.get(card_json_url(int(first_nm)))
            imt = None
            if r2.status_code == 200:
                try:
                    imt = r2.json().get("imt_id")
                except Exception:
                    pass
            rows.append({
                "step": "basket card.json", "status": r2.status_code,
                "elapsed_ms": t2.elapsed_ms, "shard": basket_for(int(first_nm)),
                "imt_id": imt,
            })
            _print_row(rows[-1])

        # 3) feedbacks v2 on the imt
        if first_root:
            host = feedbacks_host(int(first_root))
            with Timer() as t3:
                r3 = await c.get(f"https://{host}/feedbacks/v2/{first_root}")
            fb_total = 0
            if r3.status_code == 200:
                try:
                    fb_total = r3.json().get("feedbackCount", 0)
                except Exception:
                    pass
            rows.append({
                "step": f"{host}/feedbacks/v2", "status": r3.status_code,
                "elapsed_ms": t3.elapsed_ms, "feedback_count": fb_total,
            })
            _print_row(rows[-1])

    section("Verdict")
    bad = [r for r in rows if r["status"] >= 400]
    if not bad:
        ok("all three layers healthy — prod issues are app-side, not network")
    else:
        for r in bad:
            warn(f"FAIL {r['step']} → HTTP {r['status']}")
        if any(r["status"] == 429 for r in bad):
            err("hit 429 — your IP is rate-limited. wait ~2 min and retry")
        if any(r["status"] in (403, 451) for r in bad):
            err("hit 403/451 — IP / TLS check kicked in. try 09_curl_cffi_fallback.py")
    save_json("10_diagnose", {"rows": rows})
    return 0 if not bad else 1


def _print_row(r: dict) -> None:
    extra = " ".join(
        f"{k}={v}" for k, v in r.items()
        if k not in ("step", "status", "elapsed_ms")
    )
    icon = "+" if r["status"] == 200 else ("!" if r["status"] in (429,) else "-")
    print(f"  {icon} {r['step']:<28}  HTTP {r['status']:>3}  ({r['elapsed_ms']:>4} ms)  {extra}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
