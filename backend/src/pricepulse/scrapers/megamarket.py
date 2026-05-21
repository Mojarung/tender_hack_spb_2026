"""Megamarket (Сбер) adapter — default candidate for the 4th, non-formalized source.

Why Megamarket: multi-category, parses without CAPTCHA, requires only a warm
`mg_sid` cookie. Reference parser: github.com/xob0t/mmparser (Jan 2025).

This adapter is wired through `RunetScraper` (when SearXNG picks it from
top-results) and also as a direct standalone for demo robustness.
"""

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers.base import OnOffer, ScrapeResult


class MegamarketScraper:
    source: SourceKind = SourceKind.RUNET  # reported under the floating-source group

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        # TODO (hackathon):
        # 1) warm-up GET https://megamarket.ru/ with curl_cffi(chrome131) to get mg_sid
        # 2) POST https://megamarket.ru/api/mobile/v2/catalogService/catalog/search
        #    body: {"requestVersion": 10, "searchText": query, "page": 0, ...}
        # 3) parse data.items[] → name, price, image (cdn-megamarket.ru), url
        return ScrapeResult(source=self.source, offers=[])
