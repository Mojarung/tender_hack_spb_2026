"""09 — L2 stealth browser via Patchright (replaces nodriver).

PURPOSE
    When L1 is hard-blocked (every IP eventually hits this if you spam),
    we need a real Chromium that:
      - patches the Runtime.enable CDP leak (the #1 detection vector
        for Cloudflare/DataDome/Ozon)
      - removes navigator.webdriver, fixes Sec-CH-UA, canvas, WebGL
      - reuses cookies + localStorage across runs (persistent context)

    Patchright is an Apache-2.0 fork of Playwright maintained for these
    exact patches. We pick it over `nodriver` (AGPL-3.0, single-maintainer)
    for license cleanliness — see Anti-detect benchmark, May 2026:
    Patchright 25/3/3 vs nodriver 28/3/0 on Cloudflare; Ozon is not a
    Cloudflare-Enterprise tier wall, so Patchright is sufficient.

    Flow (same as antibot/browser_fetch.py, ported):
      1. Open persistent context with stealth flags + locale=ru-RU
      2. Visit https://www.ozon.ru/ — warms cookies, passes JS challenge
      3. Inject the 30-line CDP boot script (navigator/canvas/WebGL/etc)
      4. Same-origin fetch composer-api from page JS — carries cookies,
         no CORS. Returns the same widgetStates shape as L1.

USAGE
    cd ozon_research
    # first time only:
    uv run patchright install chromium
    uv run python 09_patchright_l2.py "ноутбук lenovo"

    # Headed (PowerShell) — see slider, solve once by hand:
    #   $env:HEADLESS="0"; uv run python 09_patchright_l2.py "..."
    # Headed (Linux/macOS):
    #   HEADLESS=0 uv run python 09_patchright_l2.py "..."

NOTES
    - Persistent profile lives in `ozon_research/.profile_ozon/`.
      First run will solve the JS challenge; subsequent runs reuse the
      passed cookies → much faster.
    - We do NOT auto-solve the slider here. If a slider appears on the
      first run, drive it manually with `HEADLESS=0` and the cookies
      stick for next runs. Slider auto-solve is 07+08 wired together
      (kept separate so each piece is testable in isolation).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import Timer, err, info, ok, query_from_argv, save_json, section, warn

OZON_HOME = "https://www.ozon.ru/"
COMPOSER = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
PROFILE_DIR = Path(__file__).parent / ".profile_ozon"

# --- CDP boot script (injected before every page-load) ----------------------
# Covers the high-entropy fingerprint signals that neither Patchright nor
# vanilla Playwright fix: WebGL vendor/renderer, canvas LSB noise, languages,
# hardwareConcurrency, deviceMemory, Notification quirk.
STEALTH_INIT = r"""
(() => {
  // navigator.webdriver
  try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); } catch(e){}
  // Russian-first language list
  try { Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU','ru','en-US','en'] }); } catch(e){}
  // Plausible hardware
  try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 }); } catch(e){}
  try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(e){}
  // WebGL vendor/renderer — anything realistic, not "SwiftShader"/"Mesa/llvmpipe"
  const _gp = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return _gp.call(this, p);
  };
  // Canvas LSB noise — stable within session, different across sessions
  const _toDU = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(...a) {
    try {
      const ctx = this.getContext('2d');
      if (ctx && this.width > 0 && this.height > 0) {
        const img = ctx.getImageData(0, 0, this.width, this.height);
        for (let i = 0; i < img.data.length; i += 4) img.data[i] ^= 1;
        ctx.putImageData(img, 0, 0);
      }
    } catch (e) {}
    return _toDU.apply(this, a);
  };
  // Notification permission consistency in headless
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      Object.defineProperty(Notification, 'permission', { get: () => 'denied' });
    }
  } catch(e) {}
})();
"""


async def main() -> int:
    section("PATCHRIGHT L2 — persistent stealth Chromium, same-origin fetch")

    try:
        from patchright.async_api import async_playwright
    except ImportError:
        err("patchright not installed — `pip install patchright && patchright install chromium`")
        return 3

    query = query_from_argv()
    headless = os.environ.get("HEADLESS", "1") != "0"
    info(f"query    = {query!r}")
    info(f"headless = {headless}  (set HEADLESS=0 to watch / solve slider by hand)")
    info(f"profile  = {PROFILE_DIR}  (cookies persisted here)")

    PROFILE_DIR.mkdir(exist_ok=True)
    search_path = f"/search/?text={quote(query)}&from_global=true"
    composer_url = f"{COMPOSER}?url={quote(search_path, safe='')}"
    info(f"composer = {composer_url}")

    with Timer() as t_total:
        async with async_playwright() as p:
            # launch_persistent_context = profile dir keeps cookies/localStorage
            # across runs. `channel='chrome'` uses the real Chrome binary if
            # installed; falls back to bundled Chromium otherwise.
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={"width": 1366, "height": 768},
                args=[
                    "--lang=ru-RU",
                    "--accept-lang=ru-RU,ru;q=0.9",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            await ctx.add_init_script(STEALTH_INIT)
            page = await ctx.new_page()

            info("warming session on ozon.ru ...")
            await page.goto(OZON_HOME, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            title = await page.title()
            info(f"home title = {title!r}")
            if "challenge" in title.lower() or "captcha" in title.lower():
                warn("CHALLENGE PAGE detected — re-run with HEADLESS=0 and solve once by hand")
                if headless:
                    await ctx.close()
                    return 1

            info("same-origin fetch composer-api ...")
            body_text = await page.evaluate(
                """async (url) => {
                    const r = await fetch(url, {
                        headers: {'x-o3-app-name': 'dweb_client'},
                        credentials: 'include',
                    });
                    return await r.text();
                }""",
                composer_url,
            )

            await ctx.close()

    info(f"total elapsed = {t_total.elapsed_ms} ms")
    if not body_text:
        err("empty fetch result")
        return 1

    import orjson
    try:
        body = orjson.loads(body_text)
    except orjson.JSONDecodeError:
        err("composer returned non-JSON (anti-bot stub or redirect)")
        save_json("09_l2_nonjson", {"body_preview": body_text[:2000]})
        return 1

    widget_states = body.get("widgetStates") or {}
    n_search = sum(
        1 for k in widget_states if k.startswith(("searchResultsV2", "tileGridDesktop", "skuList"))
    )
    if not n_search:
        warn(f"200 but no search widgets — keys: {list(widget_states)[:8]}")
        save_json("09_l2_no_widgets", body)
        return 2

    ok(f"got {n_search} search widget(s) via L2 Patchright")
    path = save_json("09_l2_ok", body)
    ok(f"saved → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
