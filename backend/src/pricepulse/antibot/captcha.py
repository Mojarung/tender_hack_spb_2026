"""Yandex SmartCaptcha solver via 2Captcha (paid, opt-in).

Only reachable when both `FEATURES_ALLOW_PAID` and `FEATURE_USE_2CAPTCHA`
are set — see core/features.py. Free-mode never touches this module: it
solves the Ozon slider geometrically (antibot/slider_solver.py) and other
challenges with the local Gemma 4 VLM (antibot/vlm_solver.py).

Usage (during L2 escalation)::

    iframe  = page.frame_locator("iframe[src*='smartcaptcha']")
    sitekey = await iframe.locator("[data-sitekey]").get_attribute("data-sitekey")
    token   = await CaptchaSolver(api_key).solve_yandex(sitekey, page.url)
    await page.evaluate(f"window.smartCaptcha.execute('{token}')")
"""

from __future__ import annotations

import asyncio

import structlog

from pricepulse.core.exceptions import CaptchaChallenge

log = structlog.get_logger(__name__)


class CaptchaSolver:
    """Thin async wrapper over the (synchronous) 2captcha-python client."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def solve_yandex(self, sitekey: str, page_url: str) -> str:
        """Return a SmartCaptcha token. Raises CaptchaChallenge on failure."""
        if not self.enabled:
            raise CaptchaChallenge("Captcha solving disabled (no API key configured)")
        try:
            from twocaptcha import TwoCaptcha
        except ImportError as exc:  # pragma: no cover — optional extra
            raise CaptchaChallenge(
                "2captcha-python is not installed — run `uv sync --extra captcha`"
            ) from exc

        solver = TwoCaptcha(self._api_key)

        def _solve() -> str:
            # 2captcha bills per solve; this blocks ~10-20s, hence to_thread.
            result = solver.yandex_smart(sitekey=sitekey, url=page_url)
            return str(result["code"])

        try:
            token = await asyncio.to_thread(_solve)
        except Exception as exc:  # 2captcha raises many error types
            log.warning("captcha.solve_failed", error=str(exc))
            raise CaptchaChallenge(f"2captcha solve failed: {exc}") from exc
        log.info("captcha.solved", kind="yandex_smart")
        return token


__all__ = ["CaptchaSolver"]
