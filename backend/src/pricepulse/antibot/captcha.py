"""Yandex SmartCaptcha solver wrapper.

2captcha supports Yandex SmartCaptcha out of the box.
Usage (during hackathon):
    iframe = page.frame_locator("iframe[src*='smartcaptcha']")
    sitekey = await iframe.locator("...").get_attribute("data-sitekey")
    token = await CaptchaSolver(api_key).solve_yandex(sitekey, page.url)
    await page.evaluate(f"window.smartCaptcha.execute('{token}')")
"""

from pricepulse.core.exceptions import CaptchaChallenge


class CaptchaSolver:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def solve_yandex(self, sitekey: str, page_url: str) -> str:
        if not self.enabled:
            raise CaptchaChallenge("Captcha solving disabled (no API key configured)")
        # TODO (hackathon): call twocaptcha.solver.yandex_smart(...)
        raise CaptchaChallenge("Solver not yet implemented")
