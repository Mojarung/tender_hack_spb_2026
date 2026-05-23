"""Pooled L2 stealth browser — nodriver (CDP-direct, no WebDriver).

nodriver removes Playwright/Selenium from the control plane entirely,
driving Chrome straight over the DevTools Protocol. That defeats
"automation-protocol fingerprinting" — a detection layer that patched
Playwright forks (Patchright et al.) still leak. In the May 2026
anti-detect benchmark nodriver scored 28/31 vs Cloudflare with zero hard
blocks. See CLAUDE.md → Anti-bot слой.

Used as L2 for Ozon / Yandex Market when L1 (curl_cffi) is blocked.

License note: nodriver is **AGPL-3.0**. It is imported lazily and only
when the L2 layer is actually exercised; it lives in the optional
`stealth` extra — install with `uv sync --extra stealth`.

Wire-up::

    pool = await BrowserPool.create(settings)
    async with pool.acquire("ozon") as tab:
        await tab.get("https://www.ozon.ru/...")
    await pool.aclose()
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog

from pricepulse.antibot.proxy_pool import ProxyPool

log = structlog.get_logger(__name__)

# Russian locale + the one flag worth setting explicitly. nodriver already
# strips the obvious automation tells; we deliberately do NOT override the
# user-agent or viewport — a mismatched UA is itself a detection signal.
_BROWSER_ARGS = [
    "--lang=ru-RU",
    "--accept-lang=ru-RU,ru;q=0.9",
    "--disable-blink-features=AutomationControlled",
]

# Sources that warrant the residential proxy tier.
_RESIDENTIAL_SOURCES = {"ozon", "yandex_market", "ya_market"}


class BrowserPool:
    """One shared stealth browser, many tabs, bounded by a semaphore.

    A single nodriver browser carries the warmed anti-bot session (cookies,
    passed JS challenges); tabs are cheap and isolated enough for our
    per-request fan-out. Concurrency is capped so we never open more tabs
    than the configured per-source pool sizes combined.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        proxy_pool: ProxyPool | None = None,
        max_tabs: int = 4,
    ) -> None:
        self._headless = headless
        self._proxy_pool = proxy_pool
        self._sem = asyncio.Semaphore(max(1, max_tabs))
        self._browser: Any | None = None
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, settings: Any) -> BrowserPool:
        """Build a pool from app settings. Does not launch a browser yet —
        the first :meth:`acquire` does that lazily."""
        proxy_pool: ProxyPool | None = None
        residential = getattr(settings, "residential_proxies", []) or []
        datacenter = getattr(settings, "datacenter_proxies", []) or []
        if residential or datacenter:
            proxy_pool = ProxyPool(residential, datacenter)
        max_tabs = (
            getattr(settings, "ozon_browser_pool", 2)
            + getattr(settings, "yandex_market_browser_pool", 2)
        )
        return cls(
            headless=getattr(settings, "browser_headless", True),
            proxy_pool=proxy_pool,
            max_tabs=max_tabs,
        )

    async def _ensure_browser(self) -> Any:
        async with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                import nodriver as uc
            except ImportError as exc:  # pragma: no cover — optional extra
                raise RuntimeError(
                    "nodriver is not installed — run `uv sync --extra stealth`"
                ) from exc

            args = list(_BROWSER_ARGS)
            proxy = self._proxy_pool.pick("residential") if self._proxy_pool else None
            if proxy is not None:
                # NOTE: --proxy-server takes no credentials. Use IP-authorised
                # residential endpoints, or a local auth-forwarding proxy.
                args.append(f"--proxy-server={proxy.uri}")

            self._browser = await uc.start(
                headless=self._headless,
                browser_args=args,
                lang="ru-RU",
            )
            log.info(
                "browser_pool.started",
                headless=self._headless,
                proxy=proxy.tier if proxy else "none",
            )
            return self._browser

    @asynccontextmanager
    async def acquire(self, source: str) -> AsyncIterator[Any]:
        """Yield a fresh nodriver Tab. Bounded by the pool semaphore."""
        async with self._sem:
            browser = await self._ensure_browser()
            tab = await browser.get("about:blank", new_tab=True)
            log.debug("browser_pool.tab_acquired", source=source)
            try:
                yield tab
            finally:
                try:
                    await tab.close()
                except Exception as exc:  # cleanup must not raise
                    log.warning("browser_pool.tab_close_failed", error=str(exc))

    async def aclose(self) -> None:
        async with self._lock:
            if self._browser is None:
                return
            try:
                self._browser.stop()  # nodriver: stop() is synchronous
            except Exception as exc:  # best-effort stop
                log.warning("browser_pool.stop_failed", error=str(exc))
            self._browser = None


_pool_singleton: BrowserPool | None = None
_pool_lock = asyncio.Lock()


async def get_browser_pool() -> BrowserPool:
    """Process-wide lazy singleton — one warmed browser shared across
    requests (a fresh browser per request would be slow and leak Chrome
    processes). L2 scrapers call this; the FastAPI lifespan closes it."""
    global _pool_singleton
    async with _pool_lock:
        if _pool_singleton is None:
            from pricepulse.config import get_settings

            _pool_singleton = await BrowserPool.create(get_settings())
        return _pool_singleton


async def close_browser_pool() -> None:
    """Shut the singleton browser down. Safe to call when never started."""
    global _pool_singleton
    async with _pool_lock:
        if _pool_singleton is not None:
            await _pool_singleton.aclose()
            _pool_singleton = None


__all__ = ["BrowserPool", "close_browser_pool", "get_browser_pool"]
