"""11 — WB browser cookie warmer (the PoW-solver shortcut).

PURPOSE
    WB started returning HTTP 429 with `status-no-id: PG-41-XS` and
    `Access-Control-Expose-Headers: x-pow` on `search.wb.ru/v18`.
    PG-41 is WB Page Guard's "bot signature detected" code; `x-pow`
    indicates the endpoint now expects a Proof-of-Work token computed
    client-side by the wildberries.ru JS.

    Reverse-engineering the PoW algorithm in Python is days of work.
    THE SHORTCUT: open a real headed Chrome on wildberries.ru, let the
    browser's JS compute PoW + plant whatever cookies (`__wbl`,
    `_wbauid`, `__wbpow`, …) the site uses to skip future challenges,
    then export those cookies to disk. Subsequent curl_cffi/httpx
    requests reuse them and look "human-warmed". Same pattern as the
    OzonCookieWarmer in production backend.

FLOW
    1. nodriver launches Chrome (HEADED by default; HEADLESS=1 to hide)
       with persistent user_data_dir at .profile_wb/
    2. Navigate https://www.wildberries.ru/  → triggers any JS challenge
    3. Navigate https://www.wildberries.ru/catalog/0/search.aspx?search=ноутбук
       → primes search-API cookies + lets PoW token live in the session
    4. SAME-ORIGIN fetch search.wb.ru/v18 from inside the page —
       browser auto-attaches whatever auth headers/cookies it would
       send for that endpoint. Capture the response.
    5. Export all wildberries.ru / wb.ru cookies to _out/wb_cookies.json
       (12 was the same dance for Ozon, this just swaps the targets).

USAGE
    cd wb_research
    uv run python 11_browser_warmer.py "ноутбук"

    # First run: HEADED so you can see if a Yandex SmartCaptcha pops up.
    # Solve it once if it does; the cookies will persist in .profile_wb/.
    # Subsequent runs auto-renew the PoW token without your help.

OUTPUTS
    _out/wb_cookies.json          — list of {name,value,domain,...}
    _out/wb_sample_search.json    — raw same-origin search response body
    .profile_wb/                  — persistent Chrome profile (gitignored)

EXIT CODES
    0 — got 200 from same-origin search + cookies exported
    1 — challenge unsolved or JS-fetch returned non-200
    2 — search OK but no products parsed
    3 — nodriver / Chrome missing
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
    OUT_DIR,
    Timer,
    WB_DEFAULT_DEST,
    WB_REGIONS,
    err,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

# Silence the Windows-only ResourceWarning / unraisable noise nodriver
# emits when the browser shuts down. Same trick as 12_nodriver_pro.py
# in ozon_research.
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
COOKIES_FILE = OUT_DIR / "wb_cookies.json"
SAMPLE_FILE = OUT_DIR / "wb_sample_search.json"

# CDP stealth init — silences the high-entropy fingerprint leaks nodriver
# itself doesn't patch. Trimmed down from the Ozon variant (WB doesn't
# care about canvas hashes, so we skip that one to keep latency low).
STEALTH_INIT = r"""
(() => {
  try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); } catch(e){}
  try { Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU','ru','en-US','en'] }); } catch(e){}
  try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 }); } catch(e){}
  try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(e){}
  const _gp = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return _gp.call(this, p);
  };
})();
"""


def _search_url(query: str) -> str:
    """Build the same search.wb.ru URL the production scraper uses.
    The browser will fetch this same-origin from the wildberries.ru tab
    so any required PoW/cookies attach automatically."""
    params = {
        "ab_testid": "false", "appType": "1", "curr": "rub",
        "dest": str(WB_DEFAULT_DEST), "hide_dtype": "13", "lang": "ru",
        "page": "1", "query": query, "regions": WB_REGIONS,
        "resultset": "catalog", "sort": "popular", "spp": "30",
        "suppressSpellcheck": "false",
    }
    qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return f"https://search.wb.ru/exactmatch/ru/common/v18/search?{qs}"


async def _detect_challenge(tab) -> bool:
    """Heuristic — WB occasionally serves Yandex SmartCaptcha when an
    IP is in a heavy bot list. Detect the iframe / title and ask the
    user to solve manually."""
    try:
        data = await tab.evaluate(
            """({
                title: document.title,
                hasCaptcha: !!document.querySelector(
                    'iframe[src*="captcha"], iframe[src*="smartcaptcha"], div[class*="captcha"]'
                ),
                hasBlock: document.body && (
                    document.body.innerText.includes('Доступ ограничен') ||
                    document.body.innerText.includes('Запрос отклонён')
                ),
            })""",
            await_promise=False,
        )
        if not isinstance(data, dict):
            return False
        title = (data.get("title") or "").lower()
        return (
            "captcha" in title or "доступ" in title or "забл" in title
            or bool(data.get("hasCaptcha")) or bool(data.get("hasBlock"))
        )
    except Exception:
        return False


def _to_jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "value"):
        v = v.value
    try:
        return str(v)
    except Exception:
        return None


async def _export_cookies(browser) -> list[dict]:
    """Pull all wildberries.ru / wb.ru cookies from the warmed browser."""
    try:
        raw = await browser.cookies.get_all()
    except Exception as exc:
        warn(f"cookies.get_all failed: {exc}")
        return []
    out: list[dict] = []
    for c in raw:
        try:
            domain = _to_jsonable(getattr(c, "domain", None)) or ""
            if not any(t in domain for t in ("wildberries", "wb.ru", "wbbasket")):
                continue
            out.append({
                "name": _to_jsonable(getattr(c, "name", None)),
                "value": _to_jsonable(getattr(c, "value", None)),
                "domain": domain,
                "path": _to_jsonable(getattr(c, "path", "/")),
                "secure": bool(getattr(c, "secure", False)),
                "http_only": bool(
                    getattr(c, "http_only", False) or getattr(c, "httpOnly", False)
                ),
                "same_site": _to_jsonable(
                    getattr(c, "same_site", None) or getattr(c, "sameSite", None)
                ),
                "expires": _to_jsonable(getattr(c, "expires", None)),
            })
        except Exception:
            continue
    return out


async def main() -> int:
    section("WB BROWSER WARMER — nodriver as a PoW solver shortcut")

    try:
        import nodriver as uc
    except ImportError:
        err("nodriver not installed — run `uv sync` in wb_research/")
        return 3

    query = query_from_argv("ноутбук")
    headless = os.environ.get("HEADLESS", "0") == "1"
    PROFILE_DIR.mkdir(exist_ok=True)

    info(f"query    = {query!r}")
    info(f"headless = {headless}  (HEADLESS=1 to hide; HEADED is recommended first run)")
    info(f"profile  = {PROFILE_DIR}")

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
            try:
                await tab.evaluate(STEALTH_INIT, await_promise=False)
            except Exception as exc:
                warn(f"stealth init failed (continuing): {exc}")

            info("warming wildberries.ru home …")
            await asyncio.sleep(2.5)

            if await _detect_challenge(tab):
                warn("Anti-bot challenge / captcha detected on the home page")
                if headless:
                    err("→ rerun without HEADLESS=1 and solve it manually once")
                    return 1
                info("solve it in the Chrome window, then press Enter here ↩")
                try:
                    input()
                except EOFError:
                    err("no stdin — run from an interactive terminal")
                    return 1

            # Prime the search-tab session so any search-only cookies / PoW
            # are pre-computed for the upcoming same-origin fetch.
            info("priming search page …")
            search_page_url = f"{WB_HOME}catalog/0/search.aspx?search={quote(query)}"
            search_tab = await browser.get(search_page_url, new_tab=True)
            await asyncio.sleep(3.0)

            if await _detect_challenge(search_tab):
                warn("Challenge on the search page too")
                if headless:
                    return 1
                info("solve it and press Enter ↩")
                input()

            # SAME-ORIGIN fetch of the real search-API. Browser attaches
            # whatever auth/PoW cookies it has — the response is whatever
            # the production prod-WB endpoint actually returns.
            api_url = _search_url(query)
            info(f"same-origin fetch: {api_url[:120]}…")
            js = (
                "(async () => {"
                f"  const r = await fetch({api_url!r}, {{"
                "    headers: {'Accept': '*/*'},"
                "    credentials: 'include',"
                "  });"
                "  return JSON.stringify({status: r.status, body: await r.text()});"
                "})()"
            )
            try:
                raw = await search_tab.evaluate(js, await_promise=True)
            except Exception as exc:
                err(f"same-origin fetch failed: {exc}")
                return 1

            if not isinstance(raw, str) or not raw:
                err("evaluate returned empty result")
                return 1
            try:
                fetched = json.loads(raw)
            except json.JSONDecodeError:
                err(f"fetch result was not JSON: {raw[:200]}")
                return 1

            status = fetched.get("status")
            body_text = fetched.get("body", "")
            info(f"same-origin → HTTP {status}, {len(body_text)} bytes")

            if status != 200:
                err(f"same-origin fetch returned {status}. body preview:")
                err(f"  {body_text[:300]!r}")
                save_json("11_same_origin_block", fetched)
                return 1

            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                err("body wasn't JSON")
                save_json("11_same_origin_nonjson", {"body_preview": body_text[:1000]})
                return 1

            products = (
                body.get("products")
                or (body.get("data") or {}).get("products")
                or []
            )
            if not products:
                warn("200 OK but products[] empty — soft block or genuinely no results")
                save_json("11_same_origin_no_products", body)
                return 2

            ok(f"same-origin search returned {len(products)} products")
            for i, p in enumerate(products[:5], 1):
                info(f"  {i}. nm={p.get('id')} {(p.get('name') or '')[:70]}")

            SAMPLE_FILE.write_text(
                json.dumps(body, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            ok(f"sample saved → {SAMPLE_FILE}")

            cookies = await _export_cookies(browser)
            COOKIES_FILE.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            wb_only = [c for c in cookies if "wildberries" in (c.get("domain") or "")]
            wbru = [c for c in cookies if (c.get("domain") or "").endswith(".wb.ru")]
            ok(f"exported {len(cookies)} cookies → {COOKIES_FILE.name}")
            info(f"  wildberries.ru: {len(wb_only)}, *.wb.ru: {len(wbru)}")
            interesting = sorted({
                c["name"] for c in cookies
                if c.get("name") and any(
                    tok in c["name"].lower()
                    for tok in ("wbl", "wbauid", "basket", "pow", "wbx", "wba")
                )
            })
            if interesting:
                ok(f"  load-bearing names: {', '.join(interesting)}")
            else:
                warn("  no obvious wb* cookies in jar — PoW may be header-based, not cookie-based")

    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass

    section("Done")
    ok(f"total elapsed = {t_total.elapsed_ms} ms")
    info("→ now run 12_warm_cookies_to_curl.py to verify cookies survive in curl_cffi")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
