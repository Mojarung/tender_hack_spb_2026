"""Ozon-specific browser glue: warming cookies + same-origin composer-api.

What changed (May 2026 rewrite)
-------------------------------
The old version (May 2026) tried to solve a slider CAPTCHA on every L2
fetch. Empirically nodriver + a persistent profile already auto-passes
Ozon's challenge on the first warm-up — the slider rarely shows
afterwards. That moved the architecture from "solve CAPTCHA on every
escalation" to:

    1. Warm the browser profile ONCE per TTL (default 12 h).
    2. Export Ozon's session cookies (``abt_data``, ``__Secure-ext_xcid``,
       …) from the warmed browser.
    3. Hand those cookies to ``curl_cffi`` for the fast HTTP path
       (``scrapers/ozon.py``).

Browser-side fallbacks (used when L1 fails twice in a row):

* :func:`fetch_ozon_via_browser` — same-origin ``fetch()`` of
  ``composer-api`` from inside the warmed page. Returns the parsed
  ``widgetStates`` body or ``None``.

Helpers re-used by ``scrapers/ozon.py`` and validated offline against
production payloads in ``ozon_research/12_nodriver_pro.py``:

* :func:`textrs_to_str` — render Ozon's text-runs containers.
* :func:`chars_via_structural` — extract characteristics regardless of
  widget-key naming (the 2026 PDP hides them inside ``webPdpGrid-*``
  cells, no longer in keys with ``characteristic`` in the name).
* :func:`backfill_from_pdp` — fill name/sku/image/price into an offer
  whose search row only carried a URL.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import orjson
import structlog

from pricepulse.antibot.browser_pool import get_browser_pool
from pricepulse.config import get_settings

log = structlog.get_logger(__name__)

_OZON_HOME = "https://www.ozon.ru/"
_COMPOSER_PATH = "/api/composer-api.bx/page/json/v2"

_COOKIE_CACHE_FILE = "ozon_cookies.json"


# ---------------------------------------------------------------------------
# Payload helpers (pure functions, also used by scrapers/ozon.py)
# ---------------------------------------------------------------------------
def textrs_to_str(node: Any) -> str | None:
    """Render Ozon's text-runs container `{textRs: [{type, content}]}`
    to a plain string. Always returns ``str | None`` — never lists/dicts."""
    if isinstance(node, str):
        return node or None
    if isinstance(node, list):
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


_CHAR_NAME_BLACKLIST = {
    "подробнее", "все характеристики", "в наличии", "о товаре",
    "в корзине", "ozon россия", "магазин теперь здесь",
    "перейти", "купить", "в корзину", "добавить",
}
_CHAR_VALUE_BLACKLIST = {
    "перейти", "купить", "подписаться", "в корзину", "смотреть",
}


def chars_via_structural(widget_states: dict[str, Any]) -> list[tuple[str, str]]:
    """Walk every parsed widget looking for char-shaped nodes. Handles
    both the legacy `{name, values:[{text}]}` shape AND the 2026 PDP
    shape `{title: {textRs:[{content}]}, values:[{text}]}`. Filters
    UI nav buttons that look like chars but aren't."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

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

    def _maybe_pair(node: dict[str, Any]) -> None:
        name = (
            textrs_to_str(node.get("title"))
            or textrs_to_str(node.get("name"))
            or textrs_to_str(node.get("label"))
            or textrs_to_str(node.get("titleRs"))
        )
        if not name:
            return
        if name.lower().strip() in _CHAR_NAME_BLACKLIST:
            return
        vals = node.get("values") or node.get("subtitleRs") or node.get("subtitle")
        if vals is None:
            return
        # Reject CTA "values"
        if isinstance(vals, list) and len(vals) == 1 and isinstance(vals[0], dict):
            txt = (vals[0].get("text") or "").strip().lower()
            if txt in _CHAR_VALUE_BLACKLIST:
                return
        if isinstance(vals, list):
            texts: list[str] = []
            for v in vals:
                if isinstance(v, dict):
                    t = v.get("text") or textrs_to_str(v) or v.get("value")
                    if t:
                        texts.append(str(t))
                else:
                    texts.append(str(v))
            if texts:
                _add(name, ", ".join(texts))
                return
        elif isinstance(vals, dict):
            t = textrs_to_str(vals) or vals.get("value")
            if t:
                _add(name, str(t))

    def _walk(node: Any) -> None:
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


def backfill_from_pdp(offer: dict[str, Any], widget_states: dict[str, Any]) -> None:
    """The 2026 search response often returns offers with only a URL —
    everything else (name/sku/price/image) is in the PDP widgets. Pull
    those in-place from a PDP composer-api body."""
    def _parse(k: str) -> dict[str, Any] | None:
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
                offer["name"] = textrs_to_str(title)
        if not offer.get("sku") and k.startswith("webProductMainWidget"):
            p = _parse(k) or {}
            sku = p.get("sku")
            if sku:
                offer["sku"] = str(sku)
        if k.startswith("webGallery"):
            p = _parse(k) or {}
            images = p.get("images") or p.get("coverImage")
            if isinstance(images, list) and images:
                # Collect every gallery image (modal carousel). The 2026
                # webGallery widget stores each as either a string URL
                # or {src/url: ..., type: "image"|"video"}.
                gallery: list[str] = []
                for it in images:
                    if isinstance(it, dict):
                        # skip video frames (modal carousel is images-only)
                        if it.get("type") and it.get("type") != "image":
                            continue
                        u = it.get("src") or it.get("url")
                        if isinstance(u, str):
                            gallery.append(u)
                    elif isinstance(it, str):
                        gallery.append(it)
                if gallery:
                    # de-dup while keeping order
                    seen_g: set[str] = set()
                    uniq = [u for u in gallery if not (u in seen_g or seen_g.add(u))]
                    offer.setdefault("images", []).extend(
                        u for u in uniq if u not in offer.get("images", [])
                    )
                    if not offer.get("image"):
                        offer["image"] = uniq[0]
        if not offer.get("price") and ("webPrice" in k or k.startswith("webOzonAccountPrice")):
            p = _parse(k) or {}
            for key in ("cardPrice", "price", "finalPrice"):
                val = p.get(key)
                if isinstance(val, dict):
                    val = textrs_to_str(val) or val.get("text")
                if val:
                    offer["price"] = val
                    break
        # webReviewProductScore-* carries {score, reviewsCount, totalScore}.
        # 0/null on products with no reviews — card UI hides zero ratings
        # so storing 0 doesn't cause a display glitch.
        if k.startswith("webReviewProductScore"):
            p = _parse(k) or {}
            score = p.get("score") or p.get("totalScore")
            if score is not None and not offer.get("rating"):
                try:
                    offer["rating"] = float(score)
                except (TypeError, ValueError):
                    pass
            count = p.get("reviewsCount")
            if count is not None and offer.get("reviews_count") is None:
                try:
                    offer["reviews_count"] = int(count)
                except (TypeError, ValueError):
                    pass


_REVIEW_CTA_TEXTS = {
    "перейти", "купить", "в корзину", "в корзине", "добавить", "оформить",
    "подписаться", "смотреть", "подробнее", "написать отзыв", "все отзывы",
    "оплатить", "выбрать", "сортировка", "фильтр",
}

# Only payloads from these widgets count as reviews. The reviews-API
# composer body lands them under `webListReviews-*` / `webReviewList-*`;
# nothing else. Anything else is navigation chrome with stray `text:`
# fields that look review-shaped to a generic walker.
_REVIEW_WIDGET_PREFIXES = (
    "weblistreviews", "webreviewlist", "weblistreviewsv2",
    "reviewshelfpaginator", "weblistcomments",
)


def extract_reviews(widget_states: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    """Pull review {author, score, text, date} dicts out of a reviews-API
    body. STRICT widget allow-list — pre-2026 versions were walking
    every widget with "review" in its key and matching CTA buttons
    inside `webProductMainWidget` as reviews ("Аноним: Перейти")."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str]] = set()

    def _maybe_review(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        # Ozon's 2026 review item shape: {content: {comment: {text}}}
        # or {text} or {comment} — try the common paths.
        text: str | None = None
        for path in (
            ("text",),
            ("comment",),
            ("body",),
            ("content", "comment", "text"),
            ("content", "comment"),
            ("review", "text"),
        ):
            cur: Any = item
            for k in path:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.get(k)
            if isinstance(cur, str) and cur.strip():
                text = cur
                break
            if isinstance(cur, dict):
                t = textrs_to_str(cur)
                if t:
                    text = t
                    break
        if not text:
            return None
        text = text.strip()
        # Reject CTA labels and one-word "reviews" that come from UI chrome.
        if text.lower() in _REVIEW_CTA_TEXTS or len(text) < 30:
            return None

        author = (
            item.get("author") or item.get("authorName")
            or item.get("userName") or ((item.get("user") or {}).get("name"))
        )
        if isinstance(author, dict):
            author = author.get("name") or author.get("title")
        if isinstance(author, str):
            author = author.strip() or None

        score = (
            item.get("score") or item.get("rating") or item.get("itemRating")
            or ((item.get("content") or {}).get("score")
                if isinstance(item.get("content"), dict) else None)
        )
        try:
            score = int(score) if score is not None else None
        except (TypeError, ValueError):
            score = None

        published = (
            item.get("publishedAt") or item.get("createdAt")
            or item.get("date") or item.get("publicationDate")
            or ((item.get("content") or {}).get("publishedAt")
                if isinstance(item.get("content"), dict) else None)
        )
        if isinstance(published, dict):
            published = textrs_to_str(published)

        # Review photos. Ozon stores them under a handful of shapes
        # across versions: top-level `photos`/`images`, or nested
        # under content.photos/content.images. Each entry is either a
        # string URL or a dict with src/url/image keys.
        photos: list[str] = []
        for source in (
            item.get("photos"),
            item.get("images"),
            (item.get("content") or {}).get("photos") if isinstance(item.get("content"), dict) else None,
            (item.get("content") or {}).get("images") if isinstance(item.get("content"), dict) else None,
            item.get("media"),
        ):
            if not isinstance(source, list):
                continue
            for it in source:
                if isinstance(it, str) and it.startswith(("http://", "https://")):
                    photos.append(it)
                elif isinstance(it, dict):
                    url = (
                        it.get("src") or it.get("url") or it.get("image")
                        or (it.get("photo") or {}).get("url")
                    )
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        photos.append(url)
        # de-dup, cap so we don't ship 40 photos per review
        seen_p: set[str] = set()
        photos_unique = [u for u in photos if not (u in seen_p or seen_p.add(u))][:6]

        key = (author, text[:120])
        if key in seen:
            return None
        seen.add(key)
        return {
            "author": author,
            "score": score,
            "text": text[:1000],
            "published_at": str(published) if published else None,
            "photos": photos_unique,
        }

    def _walk(node: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            r = _maybe_review(node)
            if r:
                out.append(r)
                if len(out) >= limit:
                    return
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    for k, v in widget_states.items():
        if len(out) >= limit:
            break
        if not isinstance(v, str):
            continue
        kl = k.lower()
        if not any(kl.startswith(p) for p in _REVIEW_WIDGET_PREFIXES):
            continue
        try:
            payload = orjson.loads(v)
        except orjson.JSONDecodeError:
            continue
        _walk(payload)
    return out[:limit]


# ---------------------------------------------------------------------------
# Cookie warmer — singleton with TTL + async lock
# ---------------------------------------------------------------------------
class OzonCookieWarmer:
    """Lazy nodriver-backed cookie source. One warm-up = many fast L1
    HTTP requests for the lifetime of the TTL."""

    def __init__(self, *, ttl_sec: int, profile_dir: Path) -> None:
        self._ttl = ttl_sec
        self._profile = profile_dir
        self._cookies: list[dict[str, Any]] | None = None
        self._warmed_at: float = 0.0
        self._lock = asyncio.Lock()
        self._cache_file = profile_dir / _COOKIE_CACHE_FILE

    async def get_cookies(self, *, force: bool = False) -> list[dict[str, Any]]:
        """Return warmed Ozon cookies. Warms on first call or after TTL
        expiry; force=True bypasses TTL (used on L1 403 to refresh)."""
        async with self._lock:
            if not force and self._cookies and (time.time() - self._warmed_at) < self._ttl:
                return self._cookies
            # Cold-start path: try the on-disk cache first to spare a browser launch.
            if not force and self._cookies is None and self._cache_file.exists():
                try:
                    on_disk = json.loads(self._cache_file.read_text(encoding="utf-8"))
                    if isinstance(on_disk, dict):
                        ts = float(on_disk.get("warmed_at", 0))
                        if (time.time() - ts) < self._ttl:
                            self._cookies = on_disk.get("cookies") or []
                            self._warmed_at = ts
                            log.info("ozon.cookies.loaded_from_disk", n=len(self._cookies))
                            return self._cookies
                except (json.JSONDecodeError, OSError) as exc:
                    log.warning("ozon.cookies.cache_read_failed", error=str(exc))

            self._cookies = await self._warm()
            self._warmed_at = time.time()
            self._persist()
            return self._cookies

    async def invalidate(self) -> None:
        async with self._lock:
            self._cookies = None
            self._warmed_at = 0.0

    def _persist(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            self._cache_file.write_text(
                json.dumps({"warmed_at": self._warmed_at, "cookies": self._cookies},
                           ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("ozon.cookies.cache_write_failed", error=str(exc))

    async def _warm(self) -> list[dict[str, Any]]:
        """Drive the stealth browser through ozon.ru + a search page so
        Ozon plants `abt_data` and the security tokens, then export.
        Auto-recovers if the user closed the browser between requests
        (pool detects the dead websocket and relaunches Chrome)."""
        pool = await get_browser_pool()
        async with pool.acquire("ozon") as tab:
            log.info("ozon.cookies.warming")
            try:
                await tab.get(_OZON_HOME)
                # 5 s lets Ozon's challenge page (if any) auto-resolve and
                # plant the full set of session cookies (abt_data, ext_xcid…).
                # 2 s was too short — challenge still running when we export.
                await asyncio.sleep(5.0)
                # Hit a search page too so search-only cookies (lang, region,
                # ext_xcid) get planted.
                await tab.get(_OZON_HOME + "search/?text=ноутбук&from_global=true")
                await asyncio.sleep(3.0)
            except Exception as exc:
                log.warning("ozon.cookies.warm_nav_failed", error=str(exc))
            # Export — `pool.browser` accessor instead of the private
            # `_browser` attr we used to reach for. If the browser died
            # between the navigation and here, the pool will already
            # have reset it on the next acquire(), but for this call we
            # gracefully return an empty list and let the caller retry.
            browser = pool.browser
            if browser is None:
                log.warning("ozon.cookies.no_browser_after_warm")
                return []
            try:
                raw = await browser.cookies.get_all()
            except Exception as exc:
                if pool._is_dead_browser_error(exc):
                    log.warning("ozon.cookies.browser_died_during_export")
                    await pool._reset_browser()
                else:
                    log.warning("ozon.cookies.export_failed", error=str(exc))
                return []
            out: list[dict[str, Any]] = []
            for c in raw:
                try:
                    out.append({
                        "name": _coerce(getattr(c, "name", None)),
                        "value": _coerce(getattr(c, "value", None)),
                        "domain": _coerce(getattr(c, "domain", None)),
                        "path": _coerce(getattr(c, "path", "/")),
                        "secure": bool(getattr(c, "secure", False)),
                        "http_only": bool(
                            getattr(c, "http_only", False) or getattr(c, "httpOnly", False)
                        ),
                        "same_site": _coerce(
                            getattr(c, "same_site", None) or getattr(c, "sameSite", None)
                        ),
                        "expires": _coerce(getattr(c, "expires", None)),
                    })
                except Exception as exc:
                    log.debug("ozon.cookies.skip_invalid", error=str(exc))
            ozon_only = [c for c in out if c.get("domain") and "ozon" in (c["domain"] or "")]
            log.info("ozon.cookies.warmed", total=len(out), ozon=len(ozon_only))
            return ozon_only


def _coerce(v: Any) -> Any:
    """Make nodriver enum values JSON-friendly (CookieSameSite et al)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "value"):
        v = v.value
    try:
        return str(v)
    except Exception:
        return None


_warmer_singleton: OzonCookieWarmer | None = None
_warmer_lock = asyncio.Lock()


async def get_ozon_cookie_warmer() -> OzonCookieWarmer:
    global _warmer_singleton
    async with _warmer_lock:
        if _warmer_singleton is None:
            settings = get_settings()

            def _resolve_profile() -> Path:
                p = Path(settings.ozon_profile_dir).resolve()
                p.mkdir(parents=True, exist_ok=True)
                return p

            profile = await asyncio.to_thread(_resolve_profile)
            _warmer_singleton = OzonCookieWarmer(
                ttl_sec=settings.ozon_cookie_ttl_sec,
                profile_dir=profile,
            )
        return _warmer_singleton


# ---------------------------------------------------------------------------
# Same-origin composer-api fetch (used as L2 fallback when L1 keeps 403-ing)
# ---------------------------------------------------------------------------
def _page_url_for(sub_path: str) -> str:
    """The user-facing page the browser should navigate to before the
    same-origin composer-api fetch. We strip composer-only query
    params (`layout_container`, `layout_page_index`) because they make
    the real page render an empty shell — only the API consumes them."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(sub_path)
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in {"layout_container", "layout_page_index"}
    ]
    clean = urlunsplit(("", "", parts.path, urlencode(kept), parts.fragment))
    return _OZON_HOME.rstrip("/") + (clean if clean.startswith("/") else "/" + clean)


async def fetch_ozon_via_browser(sub_path: str) -> dict[str, Any] | None:
    """Issue a composer-api fetch from inside the warmed browser page —
    carries warmed cookies, bypasses any L1-blocked TLS path. ``sub_path``
    is what you'd put after ``?url=`` (e.g. ``/search/?text=…``).

    Behaviour-critical: we navigate the tab to the matching *clean*
    user-facing page first (so anti-bot sees a normal session, and the
    page hydrates real cookies/state), THEN same-origin fetch the
    composer-api with the full sub_path including layout-container
    params. Navigating with composer-only params in the URL renders
    an empty shell and the subsequent fetch returns soft-blocked
    widgetStates."""
    pool = await get_browser_pool()
    composer_url = f"{_COMPOSER_PATH}?url={quote(sub_path, safe='')}"
    page_url = _page_url_for(sub_path)
    js = (
        "(async () => {"
        f"  const r = await fetch({composer_url!r}, "
        "    {headers: {'x-o3-app-name': 'dweb_client'}, credentials: 'include'});"
        "  return await r.text();"
        "})()"
    )
    async with pool.acquire("ozon") as tab:
        try:
            await tab.get(page_url)
            await asyncio.sleep(2.0)    # let the SPA hydrate; Ozon plants
                                        # a couple of stash cookies during JS init
            body_text = await tab.evaluate(js, await_promise=True)
        except Exception as exc:
            log.warning("ozon.browser_fetch_failed", error=str(exc))
            return None
    if not isinstance(body_text, str) or not body_text:
        return None
    try:
        return orjson.loads(body_text)
    except orjson.JSONDecodeError:
        log.warning("ozon.browser_fetch.non_json", preview=body_text[:200])
        return None


__all__ = [
    "OzonCookieWarmer",
    "backfill_from_pdp",
    "chars_via_structural",
    "extract_reviews",
    "fetch_ozon_via_browser",
    "get_ozon_cookie_warmer",
    "textrs_to_str",
]
