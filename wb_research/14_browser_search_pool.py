"""14 — WB always-on browser search pool, via CDP Network domain.

PURPOSE
    Earlier attempts:
      • fetch() from page — broke on CORS (Allow-Origin: * + credentials)
      • top-level navigation — got 429 because PoW injection only
        happens for fetch/XHR fired by the SPA bundle
      • JS monkey-patch via addScriptToEvaluateOnNewDocument — silently
        didn't fire (the SPA may use a service worker, or our tap got
        replaced by WB's own wrap, or the URLs we filtered didn't match)

    Final approach (this file): hook the **CDP Network domain**
    directly. The browser process tells us about EVERY HTTP response
    irrespective of the JS layer that issued it — fetch, XHR, beacon,
    service-worker, prefetch, image, anything. We filter by URL
    `search.wb.ru/exactmatch`, capture the body via
    `Network.getResponseBody`, and return it to Python.

    The SPA itself does the API call (with proper PoW), we just
    observe at the wire level.

USAGE
    cd wb_research
    uv run python 14_browser_search_pool.py "ноутбук" "iphone 15"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import err, info, ok, save_json, section, warn

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

STEALTH_INIT = r"""
(() => {
  try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); } catch(e){}
  try { Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU','ru','en-US','en'] }); } catch(e){}
  try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 }); } catch(e){}
  try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(e){}
})();
"""


class WBBrowserSearch:
    """Singleton-style WB search via a persistent browser tab + CDP
    Network listener.

    Threading model: a single tab handles one navigation at a time
    (serialised by `_lock`). Captured responses live in `_responses`
    keyed by request_id; we look them up after each navigation."""

    def __init__(self, *, headless: bool = False) -> None:
        self._headless = headless
        self._browser: Any = None
        self._tab: Any = None
        self._lock = asyncio.Lock()
        # request_id → {"url": str, "status": int|None, "body": str|None}
        self._responses: dict[str, dict[str, Any]] = {}
        # Track which request_ids we care about (search.wb.ru)
        self._of_interest: set[str] = set()

    async def start(self) -> None:
        if self._browser is not None:
            return
        import nodriver as uc

        PROFILE_DIR.mkdir(exist_ok=True)
        self._browser = await uc.start(
            headless=self._headless,
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
        self._tab = await self._browser.get(WB_HOME)

        # Tiny stealth — runs on every new document.
        try:
            await self._tab.send(
                uc.cdp.page.add_script_to_evaluate_on_new_document(
                    source=STEALTH_INIT,
                ),
            )
        except Exception as exc:
            warn(f"stealth init failed (continuing): {exc}")

        # Enable Network domain + register handlers.
        # Capture request URLs as they fire so we can match the right
        # `requestId` later, and pull bodies once each one finishes.
        await self._tab.send(uc.cdp.network.enable())

        def on_request_will_be_sent(event):
            try:
                url = event.request.url
            except AttributeError:
                return
            if "search.wb.ru" in url:
                rid = event.request_id
                self._of_interest.add(rid)
                self._responses[rid] = {"url": url, "status": None, "body": None}

        def on_response_received(event):
            try:
                rid = event.request_id
                if rid not in self._of_interest:
                    return
                self._responses[rid]["status"] = event.response.status
                self._responses[rid]["url"] = event.response.url
            except AttributeError:
                return

        # Capture body when the network load finishes.
        async def on_loading_finished(event):
            try:
                rid = event.request_id
            except AttributeError:
                return
            if rid not in self._of_interest:
                return
            try:
                res = await self._tab.send(
                    uc.cdp.network.get_response_body(request_id=rid),
                )
                # nodriver returns (body, base64Encoded) tuple OR an object
                if isinstance(res, tuple) and len(res) == 2:
                    body, b64 = res
                else:
                    body = getattr(res, "body", None)
                    b64 = bool(getattr(res, "base64_encoded", False))
                if b64 and isinstance(body, str):
                    import base64
                    try:
                        body = base64.b64decode(body).decode("utf-8", "replace")
                    except Exception:
                        pass
                self._responses[rid]["body"] = body or ""
            except Exception as exc:
                self._responses[rid]["body"] = ""
                self._responses[rid]["body_error"] = str(exc)

        self._tab.add_handler(uc.cdp.network.RequestWillBeSent, on_request_will_be_sent)
        self._tab.add_handler(uc.cdp.network.ResponseReceived, on_response_received)
        self._tab.add_handler(uc.cdp.network.LoadingFinished, on_loading_finished)

        # Warm the search route once so any first-load JS runs +
        # WB plants its session cookies.
        try:
            await self._tab.get(WB_HOME + "catalog/0/search.aspx?search=warmup")
            await asyncio.sleep(2.5)
        except Exception as exc:
            warn(f"warmup nav failed (continuing): {exc}")

    async def stop(self) -> None:
        async with self._lock:
            if self._browser is None:
                return
            try:
                self._browser.stop()
            except Exception:
                pass
            self._browser = None
            self._tab = None

    async def search(self, query: str) -> dict[str, Any]:
        if self._tab is None:
            raise RuntimeError("WBBrowserSearch.start() not called")

        spa_url = f"{WB_HOME}catalog/0/search.aspx?search={quote(query)}&sort=popular"

        async with self._lock:
            # Reset capture state so we only consider THIS query's calls
            self._of_interest.clear()
            self._responses.clear()

            try:
                await self._tab.get(spa_url)
            except Exception as exc:
                return {"error": f"navigation failed: {exc}"}

            # Poll for a captured 200 from search.wb.ru with a body.
            deadline = time.perf_counter() + 12.0
            while time.perf_counter() < deadline:
                await asyncio.sleep(0.4)
                for rec in self._responses.values():
                    if (
                        rec.get("status") == 200
                        and rec.get("body")
                        and "search.wb.ru" in (rec.get("url") or "")
                    ):
                        break
                else:
                    continue
                break

        # Surface what we have
        captured = list(self._responses.values())
        if not captured:
            return {
                "status": None,
                "error": (
                    "no search.wb.ru request observed in 12s — "
                    "SPA may have used SSR or a different host"
                ),
            }
        # Best 200 with parseable body
        for rec in captured:
            if rec.get("status") != 200:
                continue
            body_text = rec.get("body") or ""
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                continue
            products = (
                body.get("products")
                or (body.get("data") or {}).get("products") or []
            )
            if products:
                return {
                    "status": 200,
                    "body": body,
                    "n_captured": len(captured),
                }
        return {
            "status": None,
            "n_captured": len(captured),
            "captured_urls": [
                {
                    "url": (r.get("url") or "")[:120],
                    "status": r.get("status"),
                    "body_size": len(r.get("body") or ""),
                }
                for r in captured[:20]
            ],
        }


async def main() -> int:
    section("WB BROWSER SEARCH POOL — CDP Network observer (final)")

    try:
        import nodriver  # noqa: F401
    except ImportError:
        err("nodriver not installed — `uv sync` in wb_research/")
        return 3

    queries = sys.argv[1:] or ["ноутбук", "iphone 15", "шины 205 55 R16"]
    info(f"queries: {queries}")
    headless = os.environ.get("HEADLESS", "0") == "1"
    info(f"headless = {headless}  (HEADLESS=1 to hide; first run HEADED so you can solve any challenge)")

    poolish = WBBrowserSearch(headless=headless)
    try:
        with _Timer() as t_boot:
            await poolish.start()
        ok(f"browser boot + warm = {t_boot.ms} ms")

        outcomes: list[dict] = []
        for q in queries:
            with _Timer() as t_q:
                res = await poolish.search(q)
            elapsed = t_q.ms
            status = res.get("status")
            body = res.get("body") or {}
            products = (
                body.get("products")
                or (body.get("data") or {}).get("products") or []
            )
            n = len(products)
            outcome = "ok" if status == 200 and n else ("empty" if status == 200 else "blocked")
            outcomes.append({
                "query": q, "status": status, "n": n, "elapsed_ms": elapsed,
                "outcome": outcome,
                "n_captured": res.get("n_captured"),
                "captured_urls": res.get("captured_urls"),
                "first": [
                    {"nm": p.get("id"), "name": (p.get("name") or "")[:60]}
                    for p in products[:3]
                ],
            })
            mark = "+" if outcome == "ok" else ("?" if outcome == "empty" else "-")
            print(f"  {mark} {q!r:<40} HTTP {status} ({elapsed:>5} ms) → {n} products")
            for p in products[:3]:
                info(f"      nm={p.get('id')}  {(p.get('name') or '')[:70]}")
            # If blocked, surface the captured URLs so we can see what hit the wire
            if outcome != "ok" and res.get("captured_urls"):
                warn(f"    captured {res.get('n_captured')} responses, none usable:")
                for c in (res.get("captured_urls") or [])[:5]:
                    warn(f"      {c['status']} {c['body_size']}b  {c['url']}")
            elif outcome != "ok":
                warn(f"    {res.get('error') or 'no captured URLs at all'}")
    finally:
        await poolish.stop()

    section("Summary")
    all_ok = all(o["outcome"] == "ok" for o in outcomes)
    if all_ok:
        ok("EVERY query returned products via CDP Network observer")
        ok("→ port WBBrowserSearch class into backend/src/pricepulse/scrapers/wb.py")
    else:
        bad = [o for o in outcomes if o["outcome"] != "ok"]
        warn(f"{len(bad)} queries failed — see captured_urls in saved JSON for clues")

    save_json("14_browser_search_pool", {"queries": queries, "outcomes": outcomes})
    return 0 if all_ok else 1


class _Timer:
    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.ms = int((time.perf_counter() - self._t0) * 1000)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
