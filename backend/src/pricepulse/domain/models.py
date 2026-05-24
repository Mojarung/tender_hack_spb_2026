from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from pricepulse.domain.enums import SourceKind

ScalarAttribute = str | int | float | bool


class CanonicalAttribute(BaseModel):
    """Typed, source-traceable characteristic used for matching/ranking."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: ScalarAttribute
    unit: str | None = None
    source_key: str | None = None
    source_value: str | None = None
    confidence: float = 0.0


class CanonicalProduct(BaseModel):
    """Marketplace characteristics normalized to one comparable shape."""

    model_config = ConfigDict(frozen=True)

    category: str | None = None
    attributes: dict[str, CanonicalAttribute] = Field(default_factory=dict)
    raw: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, str] = Field(default_factory=dict)
    confidence: float = 0.0


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
    duplex: bool | None = None
    print_speed_ppm: int | None = None

    # Apparel / generic.
    apparel_type: str | None = None
    size: str | None = None
    gender: str | None = None
    material: str | None = None
    height_cm: int | None = None
    insulation: str | None = None

    # Displays / projectors / IT peripherals.
    screen_size_inch: float | None = None
    refresh_rate_hz: int | None = None
    resolution: str | None = None
    matrix_type: str | None = None
    brightness_lm: int | None = None
    interface: str | None = None
    connection_type: str | None = None

    # Other office equipment.
    security_level: str | None = None
    bin_volume_l: int | None = None
    laminating_thickness_microns: int | None = None

    # Large / home appliances.
    capacity_l: int | None = None
    power_w: int | None = None
    energy_class: str | None = None
    load_kg: float | None = None

    # Networking.
    speed_mbps: int | None = None

    # Cameras.
    megapixels: int | None = None

    # Lighting.
    color_temperature_k: int | None = None

    # Consumables / paper.
    paper_format: str | None = None
    density_gm2: int | None = None
    sheets_count: int | None = None
    pack_count: int | None = None

    # Office supplies / consumables.
    staple_size: str | None = None
    sheet_capacity: int | None = None
    binding_depth_mm: int | None = None
    page_yield: int | None = None
    original_consumable: bool | None = None
    compatible_models: str | None = None

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
    canonical_characteristics: CanonicalProduct | None = None
    attributes: ProductAttributes | None = None
    delivery: DeliveryInfo | None = None
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
    attributes: ProductAttributes | None = None
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
    score: float = Field(..., description="Composite rank score (higher = better)")
    rank: int
    deal_score: float = 0.0
    relevance_score: float = 0.5
    relevance_percent: int = 50
    rerank_score: float | None = None
    selection_reasons: list[str] = Field(default_factory=list)
    match_signals: list[str] = Field(default_factory=list)
    mismatch_signals: list[str] = Field(default_factory=list)
    unknown_signals: list[str] = Field(default_factory=list)


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
