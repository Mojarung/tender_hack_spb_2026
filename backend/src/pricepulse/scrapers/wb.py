"""Wildberries adapter.

Strategy: hit `search.wb.ru/exactmatch/ru/common/v9/search` directly via httpx.
No browser needed for WB; rotating UA + occasional `dest` change is enough.
Public, no auth.
"""

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery
from pricepulse.scrapers.base import OnOffer, ScrapeResult


class WildberriesScraper:
    source: SourceKind = SourceKind.WB

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult:
        # TODO (hackathon): call https://search.wb.ru/exactmatch/ru/common/v9/search
        # with params {query, appType=1, curr=rub, dest=-1257786, resultset=catalog,
        # sort=popular, limit}. Parse `data.products[]` → ProductOffer.
        # Build URL as https://www.wildberries.ru/catalog/{id}/detail.aspx.
        # Image: https://basket-{shard}.wb.ru/vol{...}/part{...}/{id}/images/big/1.webp
        return ScrapeResult(source=self.source, offers=[])
