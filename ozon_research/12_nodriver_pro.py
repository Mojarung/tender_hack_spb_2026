"""12 — NODRIVER PRO: real Chrome, persistent profile, full 5-product fetch.

PURPOSE
    THE bulletproof path. May-2026 anti-detect benchmark: nodriver
    28/3/0 vs Cloudflare — the only tool with zero hard blocks across
    31 tough targets. We use it as designed:
      - REAL installed Chrome (auto-detected; override with BROWSER_PATH)
      - CDP-direct, no WebDriver/Playwright shim → no protocol leak
      - Persistent profile in ./.profile_ozon/ (cookies survive runs)
      - Headed by default — set HEADLESS=1 to hide the window
      - Same-origin fetch composer-api FROM INSIDE the page → carries
        warmed cookies automatically, no CORS, no header mismatch

FLOW
    1. Launch Chrome (HEADLESS=0 by default so you can see what's going on)
    2. Navigate https://www.ozon.ru/  → triggers any anti-bot challenge
    3. If challenge appears AND headed → wait for you to solve it manually
       (one time per profile; cookies persist after that)
    4. Navigate /search/?text=<query>
    5. From page JS: fetch composer-api → parse widgetStates → 5 offers
    6. For each offer: same-origin fetch chars + reviews
    7. Export ozon_cookies.json so 13_warm_cookies_to_curl.py can
       skip the browser on subsequent runs (fast L1 mode)

USAGE
    cd ozon_research
    uv run python 12_nodriver_pro.py "ноутбук lenovo"
    HEADLESS=1 uv run python 12_nodriver_pro.py "..."     # invisible
    BROWSER_PATH="C:\\Path\\To\\chrome.exe" uv run python 12_...  # custom

OUTPUT
    _out/12_nodriver_ok.json       — 5 offers + chars + reviews
    _out/ozon_cookies.json         — cookies for 13_warm_cookies_to_curl

EXIT CODES
    0  — got >=1 offer with enrichment
    1  — challenge unsolved / fetch failed
    2  — search OK but no offers parsed
    3  — nodriver not installed / Chrome missing
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import warnings
from pathlib import Path
from urllib.parse import quote, urlparse

# Cosmetic — nodriver on Windows leaks "I/O operation on closed pipe"
# tracebacks during interpreter shutdown after browser.stop(). These are
# unraisable exceptions from __del__, NOT regular warnings, so
# `warnings.filterwarnings` doesn't catch them — we have to swap the
# unraisable hook itself. The browser is already done by then; nothing
# downstream is affected.
if sys.platform == "win32":
    warnings.filterwarnings("ignore", category=ResourceWarning)
    _orig_unraisable = sys.unraisablehook

    def _quiet_unraisable(unraisable, *, _orig=_orig_unraisable):
        exc = unraisable.exc_value
        if isinstance(exc, ValueError) and "closed pipe" in str(exc):
            return
        _orig(unraisable)

    sys.unraisablehook = _quiet_unraisable

sys.path.insert(0, str(Path(__file__).parent))

from _common import OUT_DIR, Timer, err, info, ok, query_from_argv, save_json, section, warn

OZON_HOME = "https://www.ozon.ru/"
PROFILE_DIR = Path(__file__).parent / ".profile_ozon"

LIMIT = 5
REVIEWS_PER_PRODUCT = 3
PACE_MIN_MS = 350
PACE_MAX_MS = 900


# --- stealth init: runs on every new document, defangs the high-entropy
# fingerprint leaks nodriver doesn't already cover.
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
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      Object.defineProperty(Notification, 'permission', { get: () => 'denied' });
    }
  } catch (e) {}
})();
"""


# --- widget walkers (shared with 02 / 04 / 05; redefined here so this
# script has zero project imports).
def _walk_search(widget_states: dict) -> list[dict]:
    import orjson
    out = []
    for k, v in widget_states.items():
        if not isinstance(v, str):
            continue
        if not k.startswith(("searchResultsV2", "tileGridDesktop", "skuList")):
            continue
        try:
            out.append(orjson.loads(v))
        except orjson.JSONDecodeError:
            pass
    return out


def _extract_offers(payloads, limit):
    offers, seen = [], set()
    for p in payloads:
        for item in p.get("items") or []:
            t = (item.get("cellTrackingInfo") or {}).get("product") or {}
            sku = str(t.get("id") or item.get("itemId") or "")
            if sku and sku in seen:
                continue
            link = t.get("link") or (item.get("action") or {}).get("link") or item.get("link")
            if not link:
                continue
            if link.startswith("/"):
                link = f"https://www.ozon.ru{link}"
            offers.append({
                "sku": sku,
                "name": t.get("title"),
                "price": t.get("finalPrice") or t.get("price"),
                "image": t.get("imageUrl") or t.get("image"),
                "url": link,
                "rating": t.get("rating"),
                "reviews_count": t.get("reviewsCount"),
                "seller": t.get("sellerName"),
            })
            if sku:
                seen.add(sku)
            if len(offers) >= limit:
                return offers
    return offers


def _walk_widgets(widget_states, tokens):
    """Filter widgets whose KEY matches one of `tokens`. Returns
    parsed payload dict for each match. Used when we know what we're
    looking for by widget name."""
    import orjson
    out = {}
    for k, v in widget_states.items():
        if not isinstance(v, str):
            continue
        if not any(t in k.lower() for t in tokens):
            continue
        try:
            out[k] = orjson.loads(v)
        except orjson.JSONDecodeError:
            pass
    return out


def _flatten_attrs(widgets):
    """Walk parsed widget payloads, extract {name, values}-shaped pairs.
    Tolerant of multiple shapes Ozon uses for the same idea."""
    pairs, seen = [], set()

    def _v(node):
        if isinstance(node, dict):
            name = node.get("name") or node.get("title") or node.get("label")
            values = node.get("values") or node.get("value") or node.get("texts")
            if isinstance(name, str) and values:
                if isinstance(values, list):
                    text = ", ".join(v.get("text", str(v)) if isinstance(v, dict) else str(v) for v in values)
                elif isinstance(values, dict):
                    text = values.get("text") or str(values)
                else:
                    text = str(values)
                if name.strip() and text.strip():
                    pair = (name.strip(), text.strip())
                    if pair not in seen:
                        seen.add(pair)
                        pairs.append(pair)
            for v in node.values():
                _v(v)
        elif isinstance(node, list):
            for v in node:
                _v(v)
    _v(widgets)
    return pairs


def _textrs_to_str(node) -> str | None:
    """Render Ozon's text-runs container `{textRs: [{type, content, ...}]}`
    down to a plain string. Returns None when `node` isn't one.
    Strictly returns `str | None` — never lists or dicts — so callers
    can safely do `name.lower()` etc."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        # bare textRs list: [{type, content}, ...]
        parts = [r.get("content", "") for r in node if isinstance(r, dict)]
        joined = " ".join(p for p in parts if p).strip()
        return joined or None
    if not isinstance(node, dict):
        return None
    rs = node.get("textRs")
    if isinstance(rs, list):
        parts = [r.get("content", "") for r in rs if isinstance(r, dict)]
        joined = " ".join(p for p in parts if p).strip()
        return joined or None
    t = node.get("text") or node.get("content")
    return t if isinstance(t, str) else None


def _chars_via_structural(widget_states) -> list[tuple[str, str]]:
    """Walk every parsed widget looking for char-shaped nodes. Handles
    BOTH the old `{name, values:[{text}]}` shape AND the 2026 PDP shape
    `{title: {textRs:[{content}]}, values: [{text}]}`."""
    import orjson
    pairs, seen = [], set()

    def _add(name: str, text: str) -> None:
        name = (name or "").strip()
        text = (text or "").strip()
        if not name or not text:
            return
        if len(name) > 100 or len(text) > 500:
            return
        if text.count(" ") > 25:    # likely prose, not a char value
            return
        pair = (name, text)
        if pair in seen:
            return
        seen.add(pair)
        pairs.append(pair)

    def _maybe_pair(node: dict) -> None:
        # name can be: bare string, or text-runs container under
        # node.title / node.name / node.label / node.titleRs.
        name = (
            _textrs_to_str(node.get("title"))
            or _textrs_to_str(node.get("name"))
            or _textrs_to_str(node.get("label"))
            or _textrs_to_str(node.get("titleRs"))
        )
        if not name:
            return
        # Reject UI labels masquerading as char names. The 2026 PDP
        # tree mixes nav buttons ({title: "...", values: [{text: "Перейти"}]})
        # in alongside real attributes.
        low = name.lower().strip()
        if low in {
            "подробнее", "все характеристики", "в наличии", "о товаре",
            "в корзине", "ozon россия", "магазин теперь здесь",
            "перейти", "купить", "в корзину", "добавить",
        }:
            return
        # And reject when the value is just a CTA verb
        vals_preview = node.get("values")
        if isinstance(vals_preview, list) and len(vals_preview) == 1:
            v0 = vals_preview[0]
            if isinstance(v0, dict):
                txt = (v0.get("text") or "").strip().lower()
                if txt in {"перейти", "купить", "подписаться", "в корзину", "смотреть"}:
                    return

        vals = node.get("values") or node.get("subtitleRs") or node.get("subtitle")
        if vals is None:
            return

        if isinstance(vals, list):
            texts = []
            for v in vals:
                if isinstance(v, dict):
                    t = (
                        v.get("text")                  # 2026 PDP char value
                        or _textrs_to_str(v)           # text-runs nested
                        or v.get("value")
                    )
                    if t:
                        texts.append(str(t))
                else:
                    texts.append(str(v))
            if texts:
                _add(name, ", ".join(texts))
                return
        elif isinstance(vals, dict):
            t = _textrs_to_str(vals) or vals.get("value")
            if t:
                _add(name, str(t))

    def _walk(node):
        if isinstance(node, dict):
            _maybe_pair(node)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    for v in widget_states.values():
        if not isinstance(v, str):
            continue
        try:
            payload = orjson.loads(v)
        except orjson.JSONDecodeError:
            continue
        _walk(payload)
    return pairs


def _backfill_from_pdp(offer: dict, widget_states: dict) -> None:
    """The 2026 search response often returns offers with only a URL —
    everything else (name/sku/price/image) is in the PDP widgets. Pull
    those in-place so the offer dict is usable downstream."""
    import orjson

    def _parse(k: str) -> dict | None:
        v = widget_states.get(k)
        if not isinstance(v, str):
            return None
        try:
            return orjson.loads(v)
        except orjson.JSONDecodeError:
            return None

    for k, raw in widget_states.items():
        if not isinstance(raw, str):
            continue
        if not offer.get("name") and k.startswith("webProductHeading"):
            p = _parse(k) or {}
            title = p.get("title")
            if isinstance(title, str):
                offer["name"] = title
            elif isinstance(title, dict):
                offer["name"] = _textrs_to_str(title)
        if not offer.get("sku") and k.startswith("webProductMainWidget"):
            p = _parse(k) or {}
            sku = p.get("sku")
            if sku:
                offer["sku"] = str(sku)
        if not offer.get("image") and k.startswith("webGallery"):
            p = _parse(k) or {}
            images = p.get("images") or p.get("coverImage")
            if isinstance(images, list) and images:
                first = images[0]
                if isinstance(first, dict):
                    offer["image"] = first.get("src") or first.get("url")
                elif isinstance(first, str):
                    offer["image"] = first
        if not offer.get("price") and ("webPrice" in k or k.startswith("webOzonAccountPrice")):
            p = _parse(k) or {}
            # Price text varies — try common keys
            for key in ("cardPrice", "price", "finalPrice"):
                val = p.get(key)
                if isinstance(val, dict):
                    val = _textrs_to_str(val) or val.get("text")
                if val:
                    offer["price"] = val
                    break


def _extract_reviews(widgets, limit):
    out = []

    def _r(item):
        if not isinstance(item, dict):
            return None
        text = (
            item.get("text") or item.get("comment") or item.get("body")
            or ((item.get("review") or {}).get("text") if isinstance(item.get("review"), dict) else None)
        )
        if not text:
            return None
        author = item.get("author") or item.get("authorName") or item.get("userName")
        if isinstance(author, dict):
            author = author.get("name") or author.get("title")
        score = item.get("score") or item.get("rating") or item.get("itemRating")
        return {"author": author, "score": score, "text": text[:600]}

    def _v(node):
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            r = _r(node)
            if r:
                out.append(r)
                if len(out) >= limit:
                    return
            for v in node.values():
                _v(v)
        elif isinstance(node, list):
            for v in node:
                _v(v)
    _v(widgets)
    return out[:limit]


# --- nodriver helpers --------------------------------------------------------
async def _same_origin_fetch(tab, path: str) -> dict | None:
    """Issue a fetch() from inside the page so it carries warmed cookies."""
    import orjson

    composer_url = f"/api/composer-api.bx/page/json/v2?url={quote(path, safe='')}"
    js = f"""
    (async () => {{
      try {{
        const r = await fetch({composer_url!r}, {{
          headers: {{'x-o3-app-name': 'dweb_client'}},
          credentials: 'include',
        }});
        return await r.text();
      }} catch (e) {{
        return 'FETCH_ERROR: ' + e.message;
      }}
    }})()
    """
    try:
        text = await tab.evaluate(js, await_promise=True)
    except Exception as exc:
        warn(f"  evaluate failed: {exc}")
        return None
    if not isinstance(text, str) or text.startswith("FETCH_ERROR"):
        warn(f"  fetch error: {text[:120]}")
        return None
    try:
        return orjson.loads(text)
    except orjson.JSONDecodeError:
        warn(f"  non-JSON: {text[:120]}")
        return None


async def _detect_challenge(tab) -> bool:
    """Heuristic — title or canvas-with-captcha-class signals a challenge."""
    try:
        info_js = """({
            title: document.title,
            hasCaptchaCanvas: !!document.querySelector('canvas[class*="captcha"], canvas[class*="puzzle"]'),
            hasIncapsula: document.body && document.body.innerHTML.indexOf('Incapsula') !== -1,
        })"""
        data = await tab.evaluate(info_js, await_promise=False)
        if not isinstance(data, dict):
            return False
        title = (data.get("title") or "").lower()
        return (
            "challenge" in title or "captcha" in title or "доступ" in title
            or bool(data.get("hasCaptchaCanvas")) or bool(data.get("hasIncapsula"))
        )
    except Exception:
        return False


def _to_jsonable(v):
    """Coerce nodriver-returned enums (CookieSameSite, etc.) to plain str.
    Also clamps anything stringifiable that's not JSON-native."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "value"):
        v = v.value
    try:
        return str(v)
    except Exception:
        return None


async def _export_cookies(browser, tab) -> Path:
    """Dump cookies as a list of {name, value, domain, path, ...} dicts.
    nodriver returns CookieSameSite as an enum — coerce to str."""
    raw = await browser.cookies.get_all()
    cookies = []
    for c in raw:
        try:
            cookies.append({
                "name": _to_jsonable(getattr(c, "name", None)),
                "value": _to_jsonable(getattr(c, "value", None)),
                "domain": _to_jsonable(getattr(c, "domain", None)),
                "path": _to_jsonable(getattr(c, "path", "/")),
                "secure": bool(getattr(c, "secure", False)),
                "http_only": bool(getattr(c, "http_only", False) or getattr(c, "httpOnly", False)),
                "same_site": _to_jsonable(getattr(c, "same_site", None) or getattr(c, "sameSite", None)),
                "expires": _to_jsonable(getattr(c, "expires", None)),
            })
        except Exception:
            pass
    # Keep only ozon.ru-scoped entries
    cookies = [c for c in cookies if c.get("domain") and "ozon" in (c["domain"] or "")]
    path = OUT_DIR / "ozon_cookies.json"
    path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def _pace():
    await asyncio.sleep(random.uniform(PACE_MIN_MS, PACE_MAX_MS) / 1000)


async def main() -> int:
    section("NODRIVER PRO — best 2026 stealth, full 5-product pipeline")

    try:
        import nodriver as uc
    except ImportError:
        err("nodriver not installed — `uv sync` in this folder")
        return 3

    query = query_from_argv()
    headless = os.environ.get("HEADLESS", "0") == "1"
    browser_path = os.environ.get("BROWSER_PATH") or None
    PROFILE_DIR.mkdir(exist_ok=True)

    info(f"query    = {query!r}")
    info(f"headless = {headless}    (HEADLESS=1 to hide, 0 to watch — recommended first run)")
    info(f"browser  = {browser_path or 'auto-detect (use BROWSER_PATH=… to override)'}")
    info(f"profile  = {PROFILE_DIR}")

    browser = None
    try:
        with Timer() as t_total:
            kwargs = {
                "headless": headless,
                "user_data_dir": str(PROFILE_DIR),
                "lang": "ru-RU",
                "browser_args": [
                    "--lang=ru-RU",
                    "--accept-lang=ru-RU,ru;q=0.9",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }
            if browser_path:
                kwargs["browser_executable_path"] = browser_path

            try:
                browser = await uc.start(**kwargs)
            except Exception as exc:
                err(f"failed to launch Chrome: {exc}")
                err("→ set BROWSER_PATH to your chrome.exe explicitly")
                return 3

            # Pre-page stealth init (CDP Page.addScriptToEvaluateOnNewDocument)
            try:
                main_tab = await browser.get(OZON_HOME)
                await main_tab.evaluate(STEALTH_INIT, await_promise=False)
            except Exception as exc:
                warn(f"stealth init failed (will continue): {exc}")
                main_tab = await browser.get(OZON_HOME)

            info("warming ozon.ru home ...")
            await asyncio.sleep(2.5)

            if await _detect_challenge(main_tab):
                warn("CHALLENGE detected on the home page")
                if headless:
                    err("→ rerun without HEADLESS=1 to solve it once by hand")
                    return 1
                info("solve the captcha in the browser window, then press Enter here ↩")
                try:
                    input()
                except EOFError:
                    err("no stdin — re-run from an interactive terminal")
                    return 1

            # Search page → makes Ozon plant search-related cookies + ext_xcid
            search_url = f"https://www.ozon.ru/search/?text={quote(query)}&from_global=true"
            info(f"navigating to search page ...")
            search_tab = await browser.get(search_url, new_tab=True)
            await asyncio.sleep(2.0)

            if await _detect_challenge(search_tab):
                warn("CHALLENGE on the search page too")
                if headless:
                    return 1
                info("solve it and press Enter")
                input()

            # Same-origin fetch of composer-api — carries warmed cookies
            info("fetching search composer-api (same-origin) ...")
            body = await _same_origin_fetch(search_tab, f"/search/?text={quote(query)}&from_global=true")
            if not body:
                err("composer-api fetch returned no body")
                return 1

            payloads = _walk_search(body.get("widgetStates") or {})
            offers = _extract_offers(payloads, LIMIT)
            if not offers:
                warn("no offers parsed from search response")
                save_json("12_nodriver_no_offers", body)
                return 2
            ok(f"got {len(offers)} offer(s)")

            # 1) Try known layout_container values (cheap focused fetches).
            # 2) Fall back to full PDP (no container) + STRUCTURAL walker —
            #    scans every widget and picks out {name, values} atomic
            #    pairs regardless of widget key.
            # In your last run all the tried containers returned widgets
            # like webPdpGrid-*, webProductMainWidget-* that DO contain
            # chars but never in a widget whose key matched a token
            # filter. The structural walker fixes that case.
            CHAR_CONTAINERS = [
                "pdpAtomicCharacteristics",
                "characteristicsModal",
                "fullCharacteristics",
                "pdpPage2Column",
                "pdpPage",
            ]
            CHAR_WIDGET_TOKENS = (
                "characteristic", "attribute", "shortcharacter",
                "techspec", "specifications", "params",
            )
            debug_dumped = False

            enriched: list[dict] = []
            for i, offer in enumerate(offers, 1):
                base_path = urlparse(offer["url"]).path.rstrip("/")
                info(f"  [{i}/{len(offers)}] {(offer.get('name') or '')[:60]}")

                chars: list = []
                last_keys: list[str] = []
                last_body: dict | None = None

                # --- pass A: token-filtered walk against focused containers
                for container in CHAR_CONTAINERS:
                    await _pace()
                    sub = f"{base_path}/?layout_container={container}&layout_page_index=2"
                    body = await _same_origin_fetch(search_tab, sub)
                    if not body:
                        continue
                    last_body = body
                    ws = body.get("widgetStates") or {}
                    last_keys = list(ws.keys())
                    char_widgets = _walk_widgets(ws, CHAR_WIDGET_TOKENS)
                    if char_widgets:
                        chars = _flatten_attrs(char_widgets)[:30]
                        if chars:
                            info(f"      chars via container={container} (token filter)")
                            break

                # --- pass B: full PDP + STRUCTURAL walk over all widgets
                # Always run this so we ALSO backfill name/sku/image/price
                # from the PDP — the 2026 search response often returns
                # offers with only the URL populated.
                await _pace()
                full = await _same_origin_fetch(search_tab, f"{base_path}/")
                if full:
                    last_body = full
                    ws = full.get("widgetStates") or {}
                    last_keys = list(ws.keys())
                    _backfill_from_pdp(offer, ws)
                    if not chars:
                        chars = _chars_via_structural(ws)[:30]
                        if chars:
                            info(f"      chars via structural walk over {len(last_keys)} widget(s)")

                if not chars and not debug_dumped and last_keys:
                    debug_dumped = True
                    warn(f"      still 0 chars; widget keys ({len(last_keys)} total):")
                    for k in last_keys[:25]:
                        print(f"        - {k}")
                    save_json(f"12_nodriver_chars_debug_{offer['sku']}", last_body or {})

                await _pace()
                rev_body = await _same_origin_fetch(
                    search_tab, f"{base_path}/reviews/?layout_container=reviewshelfpaginator&layout_page_index=2&page=1"
                )
                reviews = []
                if rev_body:
                    reviews = _extract_reviews(_walk_widgets(
                        rev_body.get("widgetStates") or {}, ("review", "feedback"),
                    ), REVIEWS_PER_PRODUCT)

                offer_full = dict(offer)
                offer_full["characteristics"] = chars
                offer_full["reviews"] = reviews
                enriched.append(offer_full)
                info(f"      → {len(chars)} chars, {len(reviews)} reviews")

            # Cookies for 13
            ck_path = await _export_cookies(browser, search_tab)
            ok(f"exported {ck_path.name} — feed it to 13_warm_cookies_to_curl.py")

    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass

    section("Summary")
    enriched_count = sum(1 for e in enriched if e.get("characteristics") or e.get("reviews"))
    ok(f"enriched: {enriched_count}/{len(enriched)} in {t_total.elapsed_ms} ms")
    path = save_json("12_nodriver_ok", {"query": query, "offers": enriched})
    ok(f"saved → {path}")
    return 0 if enriched_count else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
