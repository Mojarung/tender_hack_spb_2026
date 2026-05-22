"""L2 browser-driven fetch + slider-captcha solving for Ozon.

When the L1 mobile-API path (curl_cffi) is blocked, this drives the
nodriver stealth browser to:

  1. warm a real session on www.ozon.ru — passing the anti-bot challenge;
  2. solve the Ozon slider captcha geometrically if it appears
     (antibot/slider_solver.py — OpenCV, no network, ~50 ms);
  3. call the *same-origin* desktop composer-api from page JS, which
     reuses the warmed cookies and returns the same `widgetStates`
     structure the L1 parsers already understand.

Re-using the composer-api (rather than scraping the rendered DOM) keeps
L2 robust: only the warm-up + captcha touch the page, the data shape is
identical to L1.

Selectors tagged ``LIVE-CHECK`` were derived from Ozon's May 2026 layout
and should be re-verified on the hackathon network.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from urllib.parse import quote

import orjson
import structlog

from pricepulse.antibot.browser_pool import get_browser_pool
from pricepulse.antibot.slider_solver import solve_slider

log = structlog.get_logger(__name__)

_OZON_HOME = "https://www.ozon.ru/"
_COMPOSER = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"

# LIVE-CHECK — Ozon slider-challenge nodes. The background + movable piece
# are <canvas> (read via toDataURL) or <img> (read via src).
_SEL_BG = "canvas[class*='captcha'], img[class*='captcha'][class*='bg']"
_SEL_PIECE = "canvas[class*='puzzle'], img[class*='captcha'][class*='piece']"
_SEL_HANDLE = "[class*='slider'][class*='handle'], [class*='captcha'] [draggable='true']"


def _decode_data_uri(value: str | None) -> bytes | None:
    """Turn a `data:image/...;base64,...` URI into raw bytes."""
    if not value or "base64," not in value:
        return None
    try:
        return base64.b64decode(value.split("base64,", 1)[1])
    except (ValueError, IndexError):
        return None


async def _grab_challenge_images(tab: Any) -> tuple[bytes, bytes] | None:
    """Read the slider background + gap-piece images out of the page.

    Returns ``(background_bytes, piece_bytes)`` or ``None`` when no slider
    challenge is present.
    """
    js = """
    (() => {
      const grab = (el) => {
        if (!el) return null;
        if (el.tagName === 'CANVAS') return el.toDataURL('image/png');
        if (el.tagName === 'IMG') return el.src;
        return null;
      };
      return JSON.stringify({
        bg: grab(document.querySelector(__BG__)),
        piece: grab(document.querySelector(__PIECE__)),
      });
    })()
    """.replace("__BG__", repr(_SEL_BG)).replace("__PIECE__", repr(_SEL_PIECE))
    raw = await tab.evaluate(js, await_promise=False)
    if not raw:
        return None
    data = orjson.loads(raw)
    bg = _decode_data_uri(data.get("bg"))
    piece = _decode_data_uri(data.get("piece"))
    if bg is None or piece is None:
        return None
    return bg, piece


async def solve_ozon_slider(tab: Any) -> bool:
    """Detect and solve an Ozon slider captcha on `tab`.

    Returns True when there was no slider or it was solved, False when a
    slider was present but unsolved. Never raises — the caller falls back
    to the L1 result on a False.
    """
    try:
        images = await _grab_challenge_images(tab)
    except Exception as exc:  # page state is unpredictable — stay defensive
        log.warning("ozon.slider.grab_failed", error=str(exc))
        return True
    if images is None:
        return True  # no slider challenge on the page

    background, piece = images
    try:
        x_offset = solve_slider(background, piece)
    except Exception as exc:
        log.warning("ozon.slider.cv_failed", error=str(exc))
        return False
    log.info("ozon.slider.offset", x=x_offset)

    # LIVE-CHECK — drag the handle by `x_offset` px with a short, slightly
    # uneven motion so the anti-bot behavioural score stays plausible.
    try:
        handle = await tab.select(_SEL_HANDLE, timeout=3)
        if handle is None:
            return False
        await handle.scroll_into_view()
        steps = 24
        for i in range(1, steps + 1):
            await handle.mouse_move(x=x_offset * i / steps, y=0)
            await asyncio.sleep(0.012)
        await asyncio.sleep(0.6)
    except Exception as exc:
        log.warning("ozon.slider.drag_failed", error=str(exc))
        return False
    return True


async def fetch_ozon_composer(query: str) -> dict[str, Any] | None:
    """L2 fetch — return the Ozon composer-api JSON body for a search.

    Drives the stealth browser to warm a session, solves a slider if one
    appears, then fetches the desktop composer-api same-origin from page
    JS. Returns the parsed body (with `widgetStates`) or ``None``.
    """
    pool = await get_browser_pool()
    search_path = f"/search/?text={quote(query)}&from_global=true"
    composer_url = f"{_COMPOSER}?url={quote(search_path, safe='')}"

    async with pool.acquire("ozon") as tab:
        await tab.get(_OZON_HOME)
        await asyncio.sleep(1.5)  # let the anti-bot challenge render
        if not await solve_ozon_slider(tab):
            log.warning("ozon.l2.slider_unsolved")
            return None

        # Same-origin fetch — carries the warmed cookies, no CORS.
        fetch_js = f"""
        (async () => {{
          const r = await fetch({composer_url!r}, {{
            headers: {{'x-o3-app-name': 'dweb_client'}},
            credentials: 'include',
          }});
          return await r.text();
        }})()
        """
        try:
            body_text = await tab.evaluate(fetch_js, await_promise=True)
        except Exception as exc:
            log.warning("ozon.l2.fetch_failed", error=str(exc))
            return None

    if not body_text:
        return None
    try:
        return orjson.loads(body_text)
    except orjson.JSONDecodeError:
        log.warning("ozon.l2.non_json")
        return None


__all__ = ["fetch_ozon_composer", "solve_ozon_slider"]
