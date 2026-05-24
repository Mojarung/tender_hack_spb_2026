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
    # `image` / `images` are strings (not HttpUrl) so we can also carry
    # `data:image/...;base64,...` placeholders — Google Shopping ships
    # those instantly + swaps to gstatic CDN on scroll. Better to display
    # the inline thumb than fall back to the initials tile when only the
    # base64 version has loaded by the time we extract.
    image: str | None = None
    # Full product gallery — main image first. `image` is kept as the
    # "cover" alias for the card thumbnail; `images` is for the detail
    # modal carousel. Other scrapers may leave this empty.
    images: list[str] = Field(default_factory=list)
    characteristics: dict[str, str] = Field(default_factory=dict)
    seller: str | None = None
    rating: float | None = None
    # Optional product reviews (currently populated by Ozon — others may
    # add it later). Each item: {"author": str|None, "score": int|None,
    # "text": str, "published_at": str|None, "photos": list[str]}.
    # Trimmed to the top-N most recent.
    reviews: list[dict[str, str | int | list[str] | None]] = Field(default_factory=list)
    reviews_count: int | None = None
    fetched_at: datetime
    cached: bool = False
    # Similarity to the user's query, 0-100. Computed in the orchestrator
    # after the scraper returns the offer — depends on name + key
    # characteristics matching the query tokens. None means the score
    # wasn't computed (e.g. cached path, tests).
    relevance: float | None = Field(default=None, ge=0.0, le=100.0)


class SourceGroup(BaseModel):
    source: SourceKind
    count: int
    min_price: Decimal | None
    avg_price: Decimal | None = None
    median_price: Decimal | None = None
    currency: str = "RUB"
    offers: list[ProductOffer]
    error: str | None = None


class NormalizedQuery(BaseModel):
    raw: str
    normalized: str
    expansions: list[str] = Field(default_factory=list)
    alternates: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str
    max_per_source: int = Field(default=10, ge=1, le=50)
    sources: list[SourceKind] | None = None
    region_id: int = Field(
        default=213,
        ge=1,
        description="Yandex Market lr region id. Default 213 is Moscow.",
    )
    nofix: bool = Field(
        default=False,
        description="Skip typo correction and RU→EN translit — search the raw cleaned text.",
    )


class RankedOffer(BaseModel):
    offer: ProductOffer
    score: float = Field(..., description="Best-Deal Score (higher = better)")
    rank: int


class ClarificationOption(BaseModel):
    label: str = Field(..., description="Short category title with an emoji, e.g. '📱 Смартфоны Apple'")
    text: str = Field(..., description="Clarification action text, e.g. 'Искать iPhone'")
    query: str = Field(..., description="The refined query to execute")


class QueryClarification(BaseModel):
    is_ambiguous: bool
    reason: str | None = None
    options: list[ClarificationOption] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: NormalizedQuery
    groups: list[SourceGroup]
    top_deals: list[RankedOffer] = Field(
        default_factory=list,
        description="Best offers across all sources, ranked by Best-Deal Score.",
    )
    took_ms: int
    partial: bool = False
    clarification: QueryClarification | None = None

