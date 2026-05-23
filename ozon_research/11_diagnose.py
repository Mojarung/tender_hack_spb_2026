"""11 — DIAGNOSE: figure out WHY L1 failed.

PURPOSE
    02 hardened returned non-200 from your IP. Before throwing the
    full L2 browser at it, find out what Ozon actually said. This
    probes a 4×3 matrix:

        hosts:    api.ozon.ru/composer-api.bx
                  www.ozon.ru/api/composer-api.bx
                  www.ozon.ru/api/entrypoint-api.bx
                  api.ozon.ru/composer-api.bx/_action/getUserV2 (warmup-style)

        TLS:      chrome131_android   (matches mobile UA)
                  chrome131           (current production setting)
                  safari17_2_ios      (iOS fallback per curl_cffi docs)

    Plus a "minimal" headers-only check on each host (no MOBILE-GAID/
    fp/etc.) to tell whether the new headers HELPED or HURT.

    Prints a colour table:
        host × tls → status code, response-server header, body-bytes

    Saves every body to _out/ so you can grep them for clues
    ("incapsula", "checkbox-token", "challenge", etc.).

USAGE
    cd ozon_research
    uv run python 11_diagnose.py "ноутбук lenovo"

WHAT TO LOOK FOR IN THE OUTPUT
    - All hosts 403 + body says "incapsula"     → IP is in WAF block.
                                                  Use 12_nodriver_pro.
    - api.ozon.ru 403 but www.ozon.ru 200       → mobile pool blocked,
                                                  use entrypoint-api.
    - 200 but body=<2 KB, no widgetStates       → soft-block (cookies
                                                  too clean). Use 12
                                                  to warm cookies.
    - 200 + widgetStates with searchResults*    → it WORKS — your TLS
                                                  profile choice was
                                                  wrong in 02. Note
                                                  which one passed and
                                                  pin it.
    - 502/504                                   → ozon flaky, retry.
    - "x-o3-trace-id" in response headers       → request reached the
                                                  app layer (not just
                                                  WAF). Good sign.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    android_cookies,
    android_headers,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

HOSTS: list[tuple[str, str, str]] = [
    ("api-composer",     "https://api.ozon.ru/composer-api.bx/page/json/v2",        "api.ozon.ru"),
    ("www-composer",     "https://www.ozon.ru/api/composer-api.bx/page/json/v2",    "www.ozon.ru"),
    ("www-entrypoint",   "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",  "www.ozon.ru"),
]

TLS_PROFILES = ["chrome131_android", "chrome131", "safari17_2_ios"]


def _minimal_headers(host: str) -> dict[str, str]:
    """Smallest viable header set — the bare minimum the mobile API
    accepts. If this passes but the full set doesn't, one of the new
    headers is being held against us."""
    return {
        "User-Agent": "ozonapp_android/17.48.0+2528",
        "x-o3-app-name": "ozonapp_android",
        "x-o3-app-version": "17.48.0",
        "x-o3-device-type": "mobile",
        "Accept": "application/json; charset=utf-8",
        "Accept-Language": "ru",
        "Host": host,
    }


async def _probe(label: str, base: str, host: str, query: str, tls: str, *, minimal: bool) -> dict:
    """Single fetch — record everything we'd want to inspect later."""
    from curl_cffi.requests import AsyncSession
    import orjson

    sub = f"/search/?text={quote(query)}&from_global=true"
    url = f"{base}?url={quote(sub, safe='')}"
    headers = _minimal_headers(host) if minimal else android_headers(extra={"Host": host})

    result: dict = {
        "label": label, "url": url, "tls": tls, "minimal_headers": minimal,
        "status": None, "elapsed_ms": None,
        "resp_server": None, "resp_x_o3_trace": None, "set_cookie_count": 0,
        "body_bytes": 0, "is_json": False, "n_search_widgets": 0,
        "body_preview": "",
    }

    try:
        with Timer() as t:
            async with AsyncSession(impersonate=tls, timeout=12) as s:
                if not minimal:
                    for k, v in android_cookies().items():
                        s.cookies.set(k, v)
                resp = await s.get(url, headers=headers)
        result["elapsed_ms"] = t.elapsed_ms
        result["status"] = resp.status_code
        result["resp_server"] = resp.headers.get("server")
        result["resp_x_o3_trace"] = resp.headers.get("x-o3-trace-id") or resp.headers.get("x-o3-tracer-id")
        # curl_cffi exposes Set-Cookie via headers.get_list, fall back to .cookies
        try:
            result["set_cookie_count"] = len(resp.cookies)
        except Exception:
            pass
        result["body_bytes"] = len(resp.content)
        result["body_preview"] = (resp.text or "")[:600]
        try:
            body = orjson.loads(resp.content)
            result["is_json"] = True
            ws = body.get("widgetStates") or {}
            result["n_search_widgets"] = sum(
                1 for k in ws if k.startswith(("searchResultsV2", "tileGridDesktop", "skuList"))
            )
        except orjson.JSONDecodeError:
            pass
    except Exception as exc:
        result["error"] = repr(exc)

    return result


def _verdict(r: dict) -> tuple[str, str]:
    """Return (icon, terse verdict) for the row."""
    if r.get("error"):
        return "X", f"ERR {r['error'][:40]}"
    s = r["status"]
    if s == 200 and r["n_search_widgets"] > 0:
        return "+", f"OK ({r['n_search_widgets']} widgets, {r['body_bytes']}B)"
    if s == 200 and r["is_json"]:
        return "?", f"soft-block (json, 0 widgets, {r['body_bytes']}B)"
    if s == 200:
        return "?", f"non-JSON ({r['body_bytes']}B)"
    if s in (403, 451):
        return "-", f"WAF {s}"
    if s in (429,):
        return "-", f"rate-limit {s}"
    if s is None:
        return "X", "no response"
    return "-", f"HTTP {s}"


def _print_table(rows: list[dict]) -> None:
    # group by host
    by_host: dict[str, list[dict]] = {}
    for r in rows:
        by_host.setdefault(r["label"], []).append(r)

    print()
    print(f"  {'host':<16}{'tls':<22}{'mode':<10}{'verdict':<40}{'trace':<22}")
    print(f"  {'-'*16}{'-'*22}{'-'*10}{'-'*40}{'-'*22}")
    for host, items in by_host.items():
        for r in items:
            icon, verdict = _verdict(r)
            mode = "minimal" if r["minimal_headers"] else "full"
            trace = r.get("resp_x_o3_trace") or "-"
            print(f"  {icon} {host:<14}{r['tls']:<22}{mode:<10}{verdict:<40}{trace[:20]:<22}")
        print()


async def main() -> int:
    section("DIAGNOSE — probe 3 hosts × 3 TLS × 2 header modes")

    try:
        import curl_cffi  # noqa
        import orjson  # noqa
    except ImportError as exc:
        err(f"missing dep: {exc}")
        return 3

    query = query_from_argv()
    info(f"query = {query!r}")
    info("running 18 probes — 5-10 s total ...")

    coros = []
    for label, base, host in HOSTS:
        for tls in TLS_PROFILES:
            for minimal in (False, True):
                coros.append(_probe(label, base, host, query, tls, minimal=minimal))

    # Run sequentially to avoid spiking the WAF
    rows: list[dict] = []
    for c in coros:
        rows.append(await c)
        await asyncio.sleep(0.25)

    _print_table(rows)
    save_json("11_diagnose", {"query": query, "rows": rows})

    # Conclusion
    wins = [r for r in rows if r["status"] == 200 and r["n_search_widgets"] > 0]
    soft = [r for r in rows if r["status"] == 200 and r["is_json"] and r["n_search_widgets"] == 0]
    waf = [r for r in rows if r["status"] in (403, 451)]
    section("Conclusion")
    if wins:
        first = wins[0]
        ok(f"WIN: host={first['label']} tls={first['tls']} headers={'minimal' if first['minimal_headers'] else 'full'}")
        ok(f"     → pin this combo in 02_l1_hardened.py and you're done")
        return 0
    if soft:
        warn(f"{len(soft)} soft-blocks (200 but empty). IP is grey-listed.")
        warn("→ run 12_nodriver_pro.py to warm cookies from a real browser")
        return 2
    if len(waf) == len(rows):
        err("EVERY probe got WAF-blocked (403/451). IP is fully in the block list.")
        err("→ 12_nodriver_pro.py is your only path (real Chrome bypasses the WAF challenge)")
        return 1
    err("mixed failures — see _out/11_diagnose-*.json for per-row detail")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
