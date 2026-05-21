from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer

OnOffer = Callable[[ProductOffer], Awaitable[None]]


@dataclass(slots=True)
class ScrapeResult:
    source: SourceKind
    offers: list[ProductOffer] = field(default_factory=list)
    error: str | None = None
    cached: bool = False


class ScraperProtocol(Protocol):
    source: SourceKind

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: OnOffer | None = None,
    ) -> ScrapeResult: ...
