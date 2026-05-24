"""Yandex SERP — persistent stealth browser singleton.

Mirror of `wb_browser.py` / `browser_pool.py` patterns: one nodriver
Chrome with a per-process persistent profile so anti-bot cookies
(SmartCaptcha tokens etc.) survive across requests. We open
`yandex.ru/search/?text=<q>`, run an in-page JS extractor that pulls
e-commerce-flavoured organic results (cart-icon, rating, price snippets),
and hand back stub dicts.

Why a browser for Runet at all? Same reason as WB:
  - `yandex.ru/search/?text=...` HTTP-only gets a SmartCaptcha wall on
    the 2nd/3rd query from a clean session.
  - Headed Chrome with a warmed profile glides past on the first hit.

Live-validated in `runet_research/03_serp_extractor.py` and
`runet_research/05_jsonld_enrichment.py`.

Lifecycle: singleton — one browser, one tab, lock-serialised
`serp_search()` calls. Closed from the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import structlog

log = structlog.get_logger(__name__)

YANDEX_SEARCH = "https://yandex.ru/search/?text="


# Pulls e-commerce-flavoured organic results from the SERP. Same JS as
# runet_research/03 but trimmed to what production needs.
EXTRACTOR_JS = r"""
(() => {
  const items = Array.from(document.querySelectorAll('li.serp-item, .serp-item'));
  const out = [];
  for (const it of items) {
    const titleEl = it.querySelector('.OrganicTitle h2, .OrganicTitle, h2, h3');
    const linkEl  = it.querySelector('.OrganicTitle-Link, a.Link[href], a[href]');
    if (!titleEl || !linkEl) continue;
    const title = (titleEl.innerText || titleEl.textContent || '').trim();
    if (!title || title.length < 3) continue;
    let href = linkEl.getAttribute('href') || '';
    if (!href) continue;
    // Yandex wraps outbound URLs in yabs.yandex.ru/count/... — recover
    // the real shop URL from the path span.
    if (/yabs\.yandex\.ru/i.test(href)) {
      const pathEl = it.querySelector('.OrganicSubtitle-Path, .Path .Link, .Path');
      if (pathEl) {
        const realPath = (pathEl.innerText || '').trim();
        if (/^https?:\/\//i.test(realPath)) {
          href = realPath;
        } else if (realPath) {
          const host = realPath.split(/[›·•·\s]/)[0].trim();
          if (host && host.includes('.')) href = "https://" + host;
        }
      }
    }
    // Skip Yandex services (they're not "Runet" — separate aggregator)
    if (/^https?:\/\/(yandex\.ru|ya\.ru)\//i.test(href)) continue;

    // SERP-side rating / reviews_count — JSON-LD enrichment will override
    // but we keep these as fallback when product pages are anti-bot-walled.
    let rating = null, reviews_count = null;
    const ratingEl = it.querySelector('[class*=rating i], [class*=Rating]');
    if (ratingEl) {
      const text = (ratingEl.innerText || '').replace(/\s+/g, ' ').trim();
      const rm = text.match(/(\d(?:[.,]\d)?)\s*(?:из\s*5)?/);
      if (rm) rating = parseFloat(rm[1].replace(',', '.'));
      const cm = text.match(/(\d+(?:[.,]\d+)?)\s*([KkКк])?\s*(?:отзыв|оценк|review)/i);
      if (cm) {
        let n = parseFloat(cm[1].replace(',', '.'));
        if (cm[2]) n *= 1000;
        reviews_count = Math.round(n);
      }
    }
    // Only keep items Yandex itself flagged as products (cart-icon /
    // rating block) — drops Wikipedia / news / video carousels.
    const has_cart = !!it.querySelector('[class*=cart i], [class*=shop i], [class*=basket i]');
    if (!has_cart && !rating) continue;
    out.push({title, url: href, rating, reviews_count});
  }
  return JSON.stringify(out);
})()
"""


class YandexBrowserSearch:
    """Persistent nodriver browser dedicated to Yandex SERP scraping.

    Mirrors WBBrowserSearch — same lifecycle, same lock pattern.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        user_data_dir: str | None = None,
        browser_executable_path: str | None = None,
    ) -> None:
        self._headless = headless
        self._user_data_dir = user_data_dir
        self._browser_exec = browser_executable_path or None
        self._browser: Any = None
        self._tab: Any = None
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()

    @classmethod
    async def create(cls, settings: Any) -> YandexBrowserSearch:
        # Yandex profile dir defaults to ``var/profiles/yandex`` —
        # config can override via the ``yandex_profile_dir`` field.
        profile = (
            getattr(settings, "yandex_profile_dir", None)
            or "var/profiles/yandex"
        )

        def _resolve() -> str:
            p = Path(profile)
            p.mkdir(parents=True, exist_ok=True)
            return str(p.resolve())

        user_data_dir = await asyncio.to_thread(_resolve)
        return cls(
            headless=getattr(settings, "browser_headless", True),
            user_data_dir=user_data_dir,
            browser_executable_path=getattr(settings, "yandex_browser_path", "")
            or getattr(settings, "wb_browser_path", "")
            or getattr(settings, "ozon_browser_path", "")
            or None,
        )

    async def _ensure_started(self) -> None:
        if self._browser is not None and self._tab is not None:
            return
        async with self._init_lock:
            if self._browser is not None and self._tab is not None:
                return
            try:
                import nodriver as uc
            except ImportError as exc:    # pragma: no cover
                raise RuntimeError("nodriver is required for the Runet path") from exc

            kwargs: dict[str, Any] = {
                "headless": self._headless,
                "lang": "ru-RU",
                "browser_args": [
                    "--lang=ru-RU",
                    "--accept-lang=ru-RU,ru;q=0.9",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }
            if self._user_data_dir:
                kwargs["user_data_dir"] = self._user_data_dir
            if self._browser_exec:
                kwargs["browser_executable_path"] = self._browser_exec

            self._browser = await uc.start(**kwargs)
            self._tab = await self._browser.get("https://yandex.ru/")
            try:
                await self._tab.send(
                    uc.cdp.page.add_script_to_evaluate_on_new_document(
                        source=(
                            "Object.defineProperty(navigator, 'webdriver', "
                            "{ get: () => undefined });"
                        ),
                    ),
                )
            except Exception as exc:
                log.warning("yandex_browser.stealth_init_failed", error=str(exc))
            log.info(
                "yandex_browser.started",
                headless=self._headless, user_data_dir=self._user_data_dir,
            )

    def _is_dead_browser_error(self, exc: BaseException) -> bool:
        name = type(exc).__name__
        if name in {"ConnectionClosedError", "ConnectionClosed", "ConnectionClosedOK"}:
            return True
        s = str(exc).lower()
        return any(
            tok in s for tok in (
                "browser has closed", "connection closed",
                "remote endpoint closed", "websocket is closed",
            )
        )

    async def _reset(self) -> None:
        async with self._init_lock:
            if self._browser is not None:
                try:
                    self._browser.stop()
                except Exception as exc:
                    log.warning("yandex_browser.reset_stop_failed", error=str(exc))
                self._browser = None
                self._tab = None
                log.info("yandex_browser.reset")

    async def serp_search(
        self,
        query: str,
        *,
        settle_s: float = 4.0,
    ) -> dict[str, Any]:
        """Navigate to yandex.ru/search?text=<q>, run the extractor,
        return ``{"products": [...], "source": "yandex-serp"}``. On any
        failure returns ``{"error": str}`` so the caller can fall back.
        """
        await self._ensure_started()
        url = f"{YANDEX_SEARCH}{quote_plus(query)}"
        async with self._lock:
            try:
                await self._tab.get(url)
            except BaseException as exc:
                if self._is_dead_browser_error(exc):
                    log.warning("yandex_browser.dead_browser_on_nav", error=repr(exc))
                    await self._reset()
                    await self._ensure_started()
                    try:
                        await self._tab.get(url)
                    except Exception as exc2:
                        return {"error": f"nav failed after reset: {exc2}"}
                else:
                    return {"error": f"nav failed: {exc}"}

            await asyncio.sleep(settle_s)
            try:
                raw = await self._tab.evaluate(EXTRACTOR_JS, await_promise=False)
            except Exception as exc:
                return {"error": f"extractor failed: {exc}"}
            try:
                products = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except json.JSONDecodeError as exc:
                return {"error": f"extractor non-json: {exc}"}
            if not isinstance(products, list):
                products = []
            log.info("yandex_browser.serp_ok", query=query, returned=len(products))
            return {"products": products, "source": "yandex-serp"}

    async def aclose(self) -> None:
        async with self._init_lock:
            if self._browser is not None:
                try:
                    self._browser.stop()
                except Exception as exc:
                    log.warning("yandex_browser.aclose_stop_failed", error=str(exc))
            self._browser = None
            self._tab = None


_singleton: YandexBrowserSearch | None = None
_singleton_lock = asyncio.Lock()


async def get_yandex_browser() -> YandexBrowserSearch:
    global _singleton
    async with _singleton_lock:
        if _singleton is None:
            from pricepulse.config import get_settings

            _singleton = await YandexBrowserSearch.create(get_settings())
        return _singleton


async def close_yandex_browser() -> None:
    global _singleton
    async with _singleton_lock:
        if _singleton is not None:
            await _singleton.aclose()
            _singleton = None


__all__ = [
    "YandexBrowserSearch",
    "close_yandex_browser",
    "get_yandex_browser",
]
