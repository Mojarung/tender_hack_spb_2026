"""Pooled stealth browsers.

Two engines:
  - Patchright (Chromium, CDP-level patches) — for Ozon, generic Chromium-dependent sites.
  - Camoufox (Firefox, C++ fingerprint spoof + BrowserForge profiles) — for Yandex Market.

The pool returns a fresh BrowserContext bound to a sticky (proxy, UA, viewport, lang)
session. Sessions rotate after N requests or M minutes — whichever first.

Wire-up (during hackathon):
    pool = await BrowserPool.create(settings)
    async with pool.acquire("ozon") as ctx:
        page = await ctx.new_page()
        ...
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any


class BrowserPool:
    """Placeholder for the hackathon — concrete impl plugs Patchright + Camoufox."""

    @classmethod
    async def create(cls, settings: Any) -> "BrowserPool":
        return cls()

    @asynccontextmanager
    async def acquire(self, source: str) -> AsyncIterator[Any]:  # pragma: no cover
        raise NotImplementedError(
            "BrowserPool.acquire() will be implemented during the hackathon. "
            "Returns a BrowserContext from Patchright (Chromium) for 'ozon' "
            "or Camoufox (Firefox) for 'yandex_market'."
        )
        yield None  # noqa: B901 — keeps mypy happy for async-generator typing
