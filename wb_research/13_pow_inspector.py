"""13 — WB PoW inspector — what x-pow / cookies does the real browser send?

PURPOSE
    If 12_warm_cookies_to_curl.py reports that cookies alone don't pass
    (i.e. PoW is per-request, not cookie-cached), we need to *see* what
    the browser actually attaches. This script:

      1. Opens nodriver with persistent profile
      2. Attaches a CDP `Network.requestWillBeSent` listener
      3. Navigates to wildberries.ru/catalog/0/search.aspx?search=...
      4. Logs EVERY request that goes to search.wb.ru OR card.wb.ru:
         - Full URL
         - All request headers (especially x-pow, x-vehicle, x-bx-...)
         - All cookies sent
         - HTTP status of response

    With that diff in hand we know:
      • Is x-pow header present? → what value pattern?
      • Are there other custom headers (x-bx-token, x-wb-id, …)?
      • Does the token change per request?
      • Or is everything in cookies?

USAGE
    cd wb_research
    uv run python 13_pow_inspector.py "ноутбук"

OUTPUT
    _out/<ts>_13_pow_inspect.json — array of request snapshots,
    open in any editor and look for x-pow / x-bx-* / x-wb-* keys.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import warnings
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

if sys.platform == "win32":
    warnings.filterwarnings("ignore", category=ResourceWarning)
    _orig_unraisable = sys.unraisablehook

    def _quiet_unraisable(unraisable, *, _orig=_orig_unraisable):
        exc = unraisable.exc_value
        if isinstance(exc, ValueError) and "closed pipe" in str(exc):
            return
        _orig(unraisable)

    sys.unraisablehook = _quiet_unraisable


WB_HOME = "https://www.wildberries.ru/"
PROFILE_DIR = Path(__file__).parent / ".profile_wb"

# Interesting hosts to log
_HOSTS_OF_INTEREST = (
    "search.wb.ru", "card.wb.ru", "catalog.wb.ru",
    "feedbacks1.wb.ru", "feedbacks2.wb.ru",
    "static.wb.ru", "recom.wb.ru",
)


async def main() -> int:
    section("WB POW INSPECTOR — log every search.wb.ru request the browser makes")

    try:
        import nodriver as uc
    except ImportError:
        err("nodriver not installed")
        return 3

    query = query_from_argv("ноутбук")
    headless = os.environ.get("HEADLESS", "0") == "1"
    PROFILE_DIR.mkdir(exist_ok=True)
    info(f"query    = {query!r}")
    info(f"headless = {headless}")

    captured: list[dict] = []

    def _is_interest(url: str) -> bool:
        return any(h in url for h in _HOSTS_OF_INTEREST)

    browser = None
    try:
        with Timer() as t_total:
            browser = await uc.start(
                headless=headless,
                user_data_dir=str(PROFILE_DIR.resolve()),
                lang="ru-RU",
                browser_args=[
                    "--lang=ru-RU",
                    "--accept-lang=ru-RU,ru;q=0.9",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )

            tab = await browser.get(WB_HOME)
            await asyncio.sleep(2.0)

            # Hook a CDP listener via JS — nodriver doesn't expose the
            # CDP event API on Tab directly, but we can use Performance
            # Resource Timing + fetch wrapping to capture outgoing
            # requests. Inject a wrapper around the native fetch + XHR
            # that mirrors every request to window.__pp_capture.
            await tab.evaluate(
                """
                window.__pp_capture = [];
                const origFetch = window.fetch;
                window.fetch = async function(resource, init) {
                    const url = (typeof resource === 'string') ? resource : resource.url;
                    const headers = {};
                    try {
                        if (init && init.headers) {
                            const h = new Headers(init.headers);
                            h.forEach((v, k) => { headers[k] = v; });
                        } else if (resource && resource.headers) {
                            resource.headers.forEach((v, k) => { headers[k] = v; });
                        }
                    } catch (e) {}
                    const cookies = document.cookie;
                    const t0 = Date.now();
                    try {
                        const resp = await origFetch.apply(this, arguments);
                        window.__pp_capture.push({
                            via: 'fetch', url, request_headers: headers,
                            cookies_at_send: cookies,
                            status: resp.status, elapsed_ms: Date.now() - t0,
                        });
                        return resp;
                    } catch (e) {
                        window.__pp_capture.push({
                            via: 'fetch', url, request_headers: headers,
                            cookies_at_send: cookies, error: String(e),
                            elapsed_ms: Date.now() - t0,
                        });
                        throw e;
                    }
                };
                const _XHR = window.XMLHttpRequest;
                function PPXHR() {
                    const x = new _XHR();
                    const origOpen = x.open;
                    const origSetHdr = x.setRequestHeader;
                    const origSend = x.send;
                    const captured_hdrs = {};
                    let captured_url = '';
                    x.open = function(method, url, ...rest) {
                        captured_url = url;
                        return origOpen.call(this, method, url, ...rest);
                    };
                    x.setRequestHeader = function(k, v) {
                        captured_hdrs[k] = v;
                        return origSetHdr.call(this, k, v);
                    };
                    x.send = function(body) {
                        const t0 = Date.now();
                        x.addEventListener('loadend', () => {
                            window.__pp_capture.push({
                                via: 'xhr', url: captured_url,
                                request_headers: captured_hdrs,
                                cookies_at_send: document.cookie,
                                status: x.status, elapsed_ms: Date.now() - t0,
                            });
                        });
                        return origSend.call(this, body);
                    };
                    return x;
                }
                window.XMLHttpRequest = PPXHR;
                """,
                await_promise=False,
            )

            info("warming home + interceptor installed …")
            await asyncio.sleep(1.0)

            search_url = (
                f"{WB_HOME}catalog/0/search.aspx"
                f"?search={quote(query)}&sort=popular"
            )
            info(f"navigating to {search_url}")
            await browser.get(search_url, new_tab=True)
            await asyncio.sleep(4.5)    # let search-API requests fire

            # Pull captured requests from EVERY active tab
            tabs = browser.tabs
            for t in tabs:
                try:
                    raw = await t.evaluate(
                        "JSON.stringify(window.__pp_capture || [])",
                        await_promise=False,
                    )
                    if isinstance(raw, str) and raw and raw != "[]":
                        captured.extend(json.loads(raw))
                except Exception as exc:
                    info(f"  tab pull failed (skip): {exc}")
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass

    interesting = [c for c in captured if _is_interest(c.get("url", ""))]

    section("Captured")
    ok(f"total intercepted requests: {len(captured)}")
    ok(f"to *.wb.ru hosts of interest: {len(interesting)}")
    info(f"total elapsed = {t_total.elapsed_ms} ms\n")

    if not interesting:
        warn("no search.wb.ru requests fired — page may not have triggered them")
        warn("(possible: SSR-only page, or interception ran too late). Try with HEADLESS=0.")
        return 1

    custom_hdr_seen: dict[str, set[str]] = {}
    for cap in interesting:
        hdrs = cap.get("request_headers") or {}
        for k, v in hdrs.items():
            kl = k.lower()
            if kl.startswith(("x-pow", "x-bx", "x-wb", "x-vehicle", "x-real", "authorization")):
                custom_hdr_seen.setdefault(k, set()).add(str(v)[:80])

    section("Custom headers on *.wb.ru requests")
    if not custom_hdr_seen:
        warn("no x-pow / x-bx-* / x-wb-* headers observed.")
        warn("→ PoW is either not enforced from this session OR sent in cookies, not headers.")
    else:
        for k, vs in custom_hdr_seen.items():
            ok(f"  {k} = {next(iter(vs))[:80]}{'…' if len(next(iter(vs))) > 80 else ''}")
            if len(vs) > 1:
                info(f"    (saw {len(vs)} different values across requests — token rotates)")

    # Cookie diff over time
    section("Cookies sent per request")
    cookies_first = (interesting[0].get("cookies_at_send") or "").split("; ")
    cookies_last = (interesting[-1].get("cookies_at_send") or "").split("; ")
    info(f"first request: {len(cookies_first)} cookies, e.g. {[c[:40] for c in cookies_first[:6]]}")
    info(f"last  request: {len(cookies_last)} cookies, e.g. {[c[:40] for c in cookies_last[:6]]}")
    added = set(cookies_last) - set(cookies_first)
    if added:
        ok(f"  {len(added)} new cookies appeared between first and last request:")
        for c in list(added)[:8]:
            info(f"    + {c[:80]}")

    path = save_json("13_pow_inspect", {"query": query, "captured": interesting})
    ok(f"saved full capture → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
