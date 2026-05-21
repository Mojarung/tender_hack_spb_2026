"""Runet (4th, non-formalized source) adapter.

Strategy:
  • Use self-hosted Firecrawl `/v2/search` (powered by SearXNG, no Google API).
  • Filter out URLs from the 3 marketplaces — we already have those adapters.
  • For top-K results, call Firecrawl `/v2/scrape` with formats=["json"] and a
    JSON schema for {name, price, currency, image, url, characteristics}.
  • Optionally use Ollama (deepseek-r1) for LLM-extraction on weird pages.

This is THE adapter that satisfies "the 4th source cannot be fixed".
"""

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers.base import OnOffer, ScrapeResult

EXCLUDED_DOMAINS = (
    "wildberries.ru",
    "ozon.ru",
    "market.yandex.ru",
)


class RunetScraper:
    source: SourceKind = SourceKind.RUNET

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        # TODO (hackathon):
        # 1. POST {FIRECRAWL_URL}/v2/search with {"query": query.normalized + " цена",
        #    "limit": limit * 2, "sources": ["web"], "lang": "ru", "country": "ru"}.
        # 2. Filter results by URL host not in EXCLUDED_DOMAINS.
        # 3. POST {FIRECRAWL_URL}/v2/scrape with formats=["json"] and JSON schema.
        # 4. Map to ProductOffer.
        return ScrapeResult(source=self.source, offers=[])
