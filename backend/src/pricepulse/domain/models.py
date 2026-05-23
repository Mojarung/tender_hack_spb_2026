from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from pricepulse.domain.enums import SourceKind

ScalarAttribute = str | int | float | bool


class ProductAttributes(BaseModel):
    """Canonical product attributes extracted from source JSON + title text."""

    model_config = ConfigDict(frozen=True)

    category: str | None = None
    brand: str | None = None
    model: str | None = None
    color: str | None = None
    storage_gb: int | None = None
    ram_gb: int | None = None

    # Tyres.
    tyre_width_mm: int | None = None
    tyre_profile: int | None = None
    tyre_rim_inch: int | None = None
    season: str | None = None
    studs: bool | None = None

    # Office equipment.
    device_type: str | None = None
    print_technology: str | None = None
    color_print: bool | None = None
    wifi: bool | None = None

    # Apparel / generic.
    size: str | None = None
    gender: str | None = None
    material: str | None = None

    # Consumables / paper.
    paper_format: str | None = None
    density_gm2: int | None = None
    sheets_count: int | None = None

    confidence: float = 0.0
    raw: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, ScalarAttribute] = Field(default_factory=dict)


class DeliveryInfo(BaseModel):
    """Delivery signal for a user region, as reported by a marketplace."""

    model_config = ConfigDict(frozen=True)

    city: str | None = None
    region_id: str | None = None
    region_source: str | None = None
    warehouse_id: str | None = None
    distance_marketplace: int | None = None
    eta_min_hours: int | None = None
    eta_max_hours: int | None = None
    stock: int | None = None
    delivery_text: str | None = None
    confidence: float = 0.0


class ProductOffer(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: SourceKind
    name: str
    price: Decimal
    currency: str = "RUB"
    url: HttpUrl
    image: HttpUrl | None = None
    characteristics: dict[str, str] = Field(default_factory=dict)
    attributes: ProductAttributes | None = None
    delivery: DeliveryInfo | None = None
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
    attributes: ProductAttributes | None = None


class SearchRequest(BaseModel):
    query: str
    max_per_source: int = Field(default=10, ge=1, le=50)
    sources: list[SourceKind] | None = None
    city: str | None = Field(default=None, max_length=120)
    nofix: bool = Field(
        default=False,
        description="Skip typo correction and RU→EN translit — search the raw cleaned text.",
    )


class RankedOffer(BaseModel):
    offer: ProductOffer
    score: float = Field(..., description="Composite rank score (higher = better)")
    rank: int
    deal_score: float = 0.0
    relevance_score: float = 0.5
    match_signals: list[str] = Field(default_factory=list)
    mismatch_signals: list[str] = Field(default_factory=list)
    unknown_signals: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: NormalizedQuery
    groups: list[SourceGroup]
    top_deals: list[RankedOffer] = Field(
        default_factory=list,
        description="Best offers across all sources, ranked by Best-Deal Score.",
    )
    took_ms: int
    partial: bool = False
