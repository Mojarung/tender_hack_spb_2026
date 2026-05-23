"""Pooled stealth browser — nodriver, persistent per-source profile.

nodriver scored 28/3/0 in the May-2026 anti-detect benchmark vs
Cloudflare — the only OSS tool with zero hard blocks. We use it as the
warming mechanism for Ozon (and the L2 fetch path for Yandex Market).
Hackathon scope: license isolation is not a concern any more, so the
optional ``stealth`` extra is gone.

Persistence model
=================

A single nodriver browser is shared per-process. Tabs are cheap; the
browser carries the warmed anti-bot session (cookies, passed challenges).
Each *source* gets its own `user_data_dir` (under
``settings.ozon_profile_dir`` for Ozon, future per-source for Yandex
Market) so cookies and localStorage survive container restarts. A
Docker volume bound there means the bot challenge is paid once per
profile, not once per container.

Stealth init
============

Even with nodriver's CDP-direct approach, four signals still leak in
2026: WebGL vendor/renderer, canvas hash, navigator.languages on
Russian sites, and the Notification.permission quirk. ``STEALTH_INIT``
covers them via ``Page.addScriptToEvaluateOnNewDocument``. The init
runs before every navigation so SPA route changes don't undo it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog

from pricepulse.antibot.proxy_pool import ProxyPool

log = structlog.get_logger(__name__)

# Russian locale + the one flag worth setting explicitly. nodriver already
# strips the obvious automation tells; we deliberately do NOT override the
# user-agent — a mismatched UA is itself a detection signal.
_BROWSER_ARGS = [
    "--lang=ru-RU",
    "--accept-lang=ru-RU,ru;q=0.9",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]

# Sources that warrant the residential proxy tier when available.
_RESIDENTIAL_SOURCES = {"ozon", "yandex_market", "ya_market"}

# Patched on every new document via Page.addScriptToEvaluateOnNewDocument.
# Closes the four signals nodriver doesn't touch on its own. See
# ozon_research/12_nodriver_pro.py for the offline-validated equivalent.
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


class BrowserPool:
    """One shared stealth browser, many tabs, bounded by a semaphore.

    The browser is launched lazily on first :meth:`acquire`. Its
    ``user_data_dir`` is the *Ozon* profile by default since Ozon is
    where persistence matters most (Yandex Market doesn't need a warm
    profile for our use case). If you ever need a separate Yandex
    Market profile, instantiate a second :class:`BrowserPool`.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        proxy_pool: ProxyPool | None = None,
        max_tabs: int = 4,
        user_data_dir: str | None = None,
        browser_executable_path: str | None = None,
    ) -> None:
        self._headless = headless
        self._proxy_pool = proxy_pool
        self._sem = asyncio.Semaphore(max(1, max_tabs))
        self._browser: Any | None = None
        self._lock = asyncio.Lock()
        self._user_data_dir = user_data_dir
        self._browser_exec = browser_executable_path or None

    @classmethod
    async def create(cls, settings: Any) -> BrowserPool:
        proxy_pool: ProxyPool | None = None
        residential = getattr(settings, "residential_proxies", []) or []
        datacenter = getattr(settings, "datacenter_proxies", []) or []
        if residential or datacenter:
            proxy_pool = ProxyPool(residential, datacenter)
        max_tabs = (
            getattr(settings, "ozon_browser_pool", 2)
            + getattr(settings, "yandex_market_browser_pool", 2)
        )
        # Persistent profile (Ozon): mkdir up-front so nodriver doesn't
        # race against a missing path on first launch. Offloaded to a
        # thread because we're in an async context.
        profile = getattr(settings, "ozon_profile_dir", None) or "var/profiles/ozon"

        def _resolve_profile() -> str:
            p = Path(profile)
            p.mkdir(parents=True, exist_ok=True)
            return str(p.resolve())

        user_data_dir = await asyncio.to_thread(_resolve_profile)
        return cls(
            headless=getattr(settings, "browser_headless", True),
            proxy_pool=proxy_pool,
            max_tabs=max_tabs,
            user_data_dir=user_data_dir,
            browser_executable_path=getattr(settings, "ozon_browser_path", "") or None,
        )

    async def _ensure_browser(self) -> Any:
        async with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                import nodriver as uc
            except ImportError as exc:    # pragma: no cover — should now be a core dep
                raise RuntimeError(
                    "nodriver is required for the Ozon stealth path — "
                    "ensure `nodriver` is in pyproject.toml `dependencies`."
                ) from exc

            args = list(_BROWSER_ARGS)
            proxy = self._proxy_pool.pick("residential") if self._proxy_pool else None
            if proxy is not None:
                args.append(f"--proxy-server={proxy.uri}")

            kwargs: dict[str, Any] = {
                "headless": self._headless,
                "browser_args": args,
                "lang": "ru-RU",
            }
            if self._user_data_dir:
                kwargs["user_data_dir"] = self._user_data_dir
            if self._browser_exec:
                kwargs["browser_executable_path"] = self._browser_exec

            self._browser = await uc.start(**kwargs)
            log.info(
                "browser_pool.started",
                headless=self._headless,
                proxy=proxy.tier if proxy else "none",
                user_data_dir=self._user_data_dir,
            )
            return self._browser

    def _is_dead_browser_error(self, exc: BaseException) -> bool:
        """Heuristic: did this exception come from a dropped CDP
        websocket? nodriver doesn't expose a typed `BrowserClosed`, so
        we match on the websockets library's `ConnectionClosed*`
        family plus a couple of error-message fingerprints we've
        observed when the user closes the Chrome window manually."""
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

    async def _reset_browser(self) -> None:
        """Drop the dead browser reference so the next _ensure_browser()
        relaunches Chrome from scratch. Caller already holds the lock or
        is OK racing for it."""
        async with self._lock:
            if self._browser is not None:
                try:
                    self._browser.stop()
                except Exception as exc:
                    log.warning("browser_pool.reset_stop_failed", error=str(exc))
                self._browser = None
                log.info("browser_pool.reset")

    @asynccontextmanager
    async def acquire(self, source: str) -> AsyncIterator[Any]:
        """Yield a fresh nodriver Tab with STEALTH_INIT already injected.
        If the underlying browser is dead (user closed the Chrome window,
        process crashed, CDP websocket dropped), tear it down and
        relaunch once."""
        async with self._sem:
            browser = await self._ensure_browser()
            tab = None
            try:
                tab = await browser.get("about:blank", new_tab=True)
            except BaseException as exc:
                if self._is_dead_browser_error(exc):
                    log.warning(
                        "browser_pool.dead_browser_detected",
                        error=repr(exc), source=source,
                    )
                    await self._reset_browser()
                    browser = await self._ensure_browser()
                    tab = await browser.get("about:blank", new_tab=True)
                else:
                    raise
            try:
                try:
                    await tab.evaluate(STEALTH_INIT, await_promise=False)
                except Exception as exc:
                    log.warning("browser_pool.stealth_init_failed", error=str(exc))
                log.debug("browser_pool.tab_acquired", source=source)
                yield tab
            finally:
                try:
                    await tab.close()
                except Exception as exc:
                    log.warning("browser_pool.tab_close_failed", error=str(exc))

    async def aclose(self) -> None:
        async with self._lock:
            if self._browser is None:
                return
            try:
                self._browser.stop()
            except Exception as exc:
                log.warning("browser_pool.stop_failed", error=str(exc))
            self._browser = None

    @property
    def browser(self) -> Any | None:
        """Direct access for callers that need browser-level APIs
        (cookies.get_all etc.). Returns None when not initialised."""
        return self._browser


_pool_singleton: BrowserPool | None = None
_pool_lock = asyncio.Lock()


async def get_browser_pool() -> BrowserPool:
    """Process-wide lazy singleton. L2 scrapers call this; the FastAPI
    lifespan closes it via :func:`close_browser_pool`."""
    global _pool_singleton
    async with _pool_lock:
        if _pool_singleton is None:
            from pricepulse.config import get_settings

            _pool_singleton = await BrowserPool.create(get_settings())
        return _pool_singleton


async def close_browser_pool() -> None:
    global _pool_singleton
    async with _pool_lock:
        if _pool_singleton is not None:
            await _pool_singleton.aclose()
            _pool_singleton = None


__all__ = [
    "STEALTH_INIT",
    "BrowserPool",
    "close_browser_pool",
    "get_browser_pool",
]
