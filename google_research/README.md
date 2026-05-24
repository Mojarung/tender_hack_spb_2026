# Google research — Google Shopping SERP

Isolated uv sandbox (mirror of `runet_research/`, `ozon_research/`,
`wb_research/`). Goal: scrape Google Shopping (`tbm=shop`) cards directly
— price/url/image/seller all live inline in the SERP, no per-shop
enrichment needed.

Why Google over Yandex for the 4-th source: Yandex SERP only inlines
shop name + rating; Google Shopping inlines the actual product card
(name, price, image, seller, sometimes rating). One browser pass, full
ProductOffer ready, no curl_cffi anti-bot dance per shop.

## Scripts

| # | File | Goal |
|---|---|---|
| 01 | `01_shopping_baseline.py` | Open Google Shopping, screenshot, confirm not captcha-walled |
| 02 | `02_card_dump.py` | Dump the actual card DOM so we know which selectors hold price/url/image |
| 03 | `03_extractor.py` | End-to-end: search → cards → JSON |

## Usage

```bash
cd google_research
uv sync
uv run python 01_shopping_baseline.py "iphone 15 купить"
```
