"""Ozon adapter.

Strategy:
  1) Try `api.ozon.ru/composer-api.bx/page/json/v2?url=/search/?text=...` via httpx
     with a realistic browser header set + residential proxy.
  2) On 403 / JS challenge / DataDome — fallback to Patchright (Chromium CDP-stealth).

DataDome on Ozon is aggressive; Patchright is the most effective bypass tool
as of 2026-05.
"""

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers.base import OnOffer, ScrapeResult


class OzonScraper:
    source: SourceKind = SourceKind.OZON

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        # TODO (hackathon):
        # phase 1: httpx GET composer-api with X-O3-App-Name, X-O3-App-Version,
        #          residential proxy, realistic Accept-Language, sec-ch-ua headers.
        # phase 2: on detection, hand off to antibot.browser_pool.acquire("ozon")
        #          which gives a Patchright context with sticky fingerprint.
        return ScrapeResult(source=self.source, offers=[])
