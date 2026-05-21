"""Yandex Market adapter.

Strategy:
  • Camoufox (Firefox C++ stealth) — best 2026 option vs. Yandex SmartCaptcha.
  • Residential RU/CIS proxy, sticky per-session.
  • On SmartCaptcha: extract sitekey from iframe, solve via 2captcha, inject token.
  • DOM parsing through selectors module (Маркет переписывает разметку часто).
"""

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers.base import OnOffer, ScrapeResult


class YandexMarketScraper:
    source: SourceKind = SourceKind.YA_MARKET

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        # TODO (hackathon):
        # 1. browser = await antibot.browser_pool.acquire("yandex_market")  # Camoufox
        # 2. page.goto(f"https://market.yandex.ru/search?text={quote(query.normalized)}")
        # 3. await antibot.captcha.solve_if_present(page)
        # 4. parse cards via selectors module; emit ProductOffer per card.
        return ScrapeResult(source=self.source, offers=[])
