# Runet Web Search Redesign

## Goal

Make the fourth Runet source reliable without external search/scraping APIs and without product-level hardcoding.

Required jury categories:

- Apparel / shoes.
- Tyres.
- Office equipment and consumables.
- Electronics as a control category.

The source must return several relevant offers with product name, price, image, URL, and characteristics.

## Current Baseline

The current path is:

```text
SearXNG -> candidate URLs -> fetch pages -> JSON-LD Product -> ProductOffer
```

Live diagnostics on 12 category queries returned `0/12` offers because SearXNG upstream engines were unavailable:

- Google: CAPTCHA.
- DuckDuckGo: CAPTCHA.
- Brave: 429 / too many requests.
- Qwant / Presearch: access denied.
- Mojeek: timeout.

This means SearXNG cannot be the primary discovery layer. It should stay as optional cold-start/background discovery only.

## Tested Alternative

Robots/sitemap discovery works much better for Runet shops. A bounded read-only probe collected tens of thousands of product-like URLs from public sitemaps.

Confirmed useful sources:

- Tyres: `koleso.ru`, `shinservice.ru`, `kolesa-darom.ru`, `4tochki.ru`.
- Office: `kns.ru`, `global-cartridge.ru`, `cartridge.ru`, `officemag.ru`.
- Apparel/shoes: `groupprice.ru`, `respect-shoes.ru`, `street-beat.ru`, `sneakerhead.ru`, `rendez-vous.ru`.
- Electronics/control: `doctorhead.ru`, `cmstore.ru`, `pitergsm.ru`, `technopark.ru`.

Product/listing pages usually contain data, but not always in JSON-LD:

- `koleso.ru`: Next data, microdata-like product blocks, visible prices, images, tyre specs.
- `shinservice.ru`: Next data, listing prices and tyre filter/spec state.
- `kns.ru`: microdata/Product + HTML prices/images/specs.
- `global-cartridge.ru`: JSON-LD on some products, HTML specs/images/prices.
- `groupprice.ru`: clean JSON-LD Product for apparel.
- `respect-shoes.ru`: microdata Product with price/image/sku/availability.
- `street-beat.ru`: embedded `window.digitalData` with name, price, image, stock, category.
- `sneakerhead.ru`: OpenGraph/product meta + HTML specs.

## Target Architecture

```text
NormalizedQuery
  -> local offer snapshot cache/index
  -> provider registry
  -> robots/sitemap/feed discovery
  -> URL slug ranking
  -> live fetch/enrich top URLs
  -> multi-stage extraction
  -> relevance scoring
  -> ProductOffer[]
  -> SearXNG fallback only if needed
```

## Provider Registry

Store domains and discovery hints, not products:

```yaml
providers:
  - domain: koleso.ru
    categories: [tyres]
    priority: 90
    discovery:
      robots: true
      sitemap: true
      search_url: null
    enabled: true
```

This is source-level configuration, not query/product hardcoding.

## Discovery Order

1. Search hot Redis/Postgres snapshots.
2. Rank known sitemap URLs by query tokens.
3. Fetch/enrich top product URLs.
4. Try provider search pages where allowed and stable.
5. Use SearXNG as last fallback only.

## Extraction Pipeline

Do not convert HTML directly into `ProductOffer`. First produce internal candidates:

```python
class ExtractedCandidate:
    name: str | None
    price: Decimal | None
    currency: str | None
    url: str | None
    image: str | None
    characteristics: dict[str, str]
    source_stage: str
    confidence: float
```

Extraction order:

1. JSON-LD `Product`, `Offer`, `AggregateOffer`, `ItemList`.
2. Microdata/RDFa Schema.org Product.
3. Embedded state: `__NEXT_DATA__`, `__NUXT_DATA__`, `window.__NUXT__`, `window.__PRELOADED_STATE__`, `window.digitalData`.
4. OpenGraph/product meta.
5. HTML product page heuristics: `h1`, price blocks, product images, spec tables.
6. HTML listing cards.

## Relevance Rules

Hard guards:

- Query numeric/model tokens must match product title or attributes.
- `iphone 15 128` must not match `iphone 14 128` or `iphone 16 128`.
- `205/55 R16` must not match other tyre sizes.
- `принтер hp laserjet` must not match cartridge unless query includes cartridge/toner.
- Excluded marketplaces must never count as the fourth source.

Scoring components:

- Token overlap.
- Strong token match: numbers, model names, tyre sizes, storage, cartridge models, sizes.
- Attribute match from existing `ProductAttributes`.
- Category match.
- Extraction confidence.

## Image Strategy

Normalize:

- Protocol-relative URLs.
- Relative image URLs.
- HTML entities.
- `srcset` best candidate.

Reject obvious bad images:

- logo, icon, sprite, placeholder, no-photo, loader, favicon, tiny SVGs.

Validate only final/top candidates with `HEAD` or small `GET` range and cache validation results.

## Test Plan

Manual diagnostics script:

```bash
uv run python scripts/runet_websearch_diagnostics.py --mode all
```

Representative queries:

- `шины 205/55 R16 зимние шипованные`
- `шины 225/45 R17 летние`
- `шины R15 зимние`
- `принтер лазерный цветной wifi`
- `картридж Canon 725`
- `бумага A4 80 г/м2 500 листов`
- `футболка мужская хлопок черная размер M`
- `кроссовки nike мужские 42`
- `куртка женская зимняя`

Success criteria:

- At least 8/10 category queries return a valid Runet offer after index/provider fallback is implemented.
- At least 70% of offers have valid price and image.
- At least 50% of offers have non-trivial characteristics.
- No excluded marketplaces.
- Numeric/model constraints hold.

## Recommended Build Sequence

1. Keep the `RunetScraper` public interface unchanged.
2. Add provider/sitemap discovery fallback before SearXNG.
3. Add generic meta/microdata/HTML extraction for product pages.
4. Add embedded-state extraction for `__NEXT_DATA__` and `window.digitalData`.
5. Add persistent local index/snapshots.
6. Add background prewarm task.
7. Demote SearXNG to fallback/background seeding.
