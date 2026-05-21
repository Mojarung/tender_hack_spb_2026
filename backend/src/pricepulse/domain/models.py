from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from pricepulse.domain.enums import SourceKind


class ProductOffer(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: SourceKind
    name: str
    price: Decimal
    currency: str = "RUB"
    url: HttpUrl
    image: HttpUrl | None = None
    characteristics: dict[str, str] = Field(default_factory=dict)
    seller: str | None = None
    rating: float | None = None
    fetched_at: datetime
    cached: bool = False


class SourceGroup(BaseModel):
    source: SourceKind
    count: int
    min_price: Decimal | None
    median_price: Decimal | None = None
    currency: str = "RUB"
    offers: list[ProductOffer]
    error: str | None = None


class NormalizedQuery(BaseModel):
    raw: str
    normalized: str
    expansions: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str
    max_per_source: int = Field(default=10, ge=1, le=50)
    sources: list[SourceKind] | None = None
    nofix: bool = Field(
        default=False,
        description="Skip typo correction and RU→EN translit — search the raw cleaned text.",
    )


class RankedOffer(BaseModel):
    offer: ProductOffer
    score: float = Field(..., description="Best-Deal Score (higher = better)")
    rank: int


class SearchResponse(BaseModel):
    query: NormalizedQuery
    groups: list[SourceGroup]
    top_deals: list[RankedOffer] = Field(
        default_factory=list,
        description="Best offers across all sources, ranked by Best-Deal Score.",
    )
    took_ms: int
    partial: bool = False
