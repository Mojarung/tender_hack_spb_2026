"""Unit tests for Runet extraction helpers.

These tests are offline — no real network calls. They verify that each
extraction stage (JSON-LD, __NEXT_DATA__, digitalData, microdata, HTML)
correctly produces ProductOffer objects from crafted HTML snippets.
"""

from __future__ import annotations

import json
from decimal import Decimal

from pricepulse.scrapers.runet import (
    _html_offer,
    _name_matches_query,
    _offers_from_digital_data,
    _offers_from_microdata,
    _offers_from_next_data,
    _to_offer,
    _tokenize,
    _walk_jsonld,
)


def _q(text: str) -> set[str]:
    return _tokenize(text)


# ---------------------------------------------------------------------------
# JSON-LD extraction (existing path)
# ---------------------------------------------------------------------------

_JSONLD_PRODUCT_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Шина Nokian Nordman 7 205/55 R16 94T",
  "offers": {"@type": "Offer", "price": "4990", "priceCurrency": "RUB"}
}
</script>
</head></html>
"""


def test_jsonld_product_extracted() -> None:
    payloads = list(_walk_jsonld(_JSONLD_PRODUCT_HTML))
    assert len(payloads) == 1
    offer = _to_offer(
        "https://koleso.ru/product/1/",
        payloads[0],
        query_tokens=_q("шины 205/55 R16 зимние"),
    )
    assert offer is not None
    assert offer.price == Decimal("4990")
    assert "205" in offer.name or "Nokian" in offer.name


# ---------------------------------------------------------------------------
# __NEXT_DATA__ extraction
# ---------------------------------------------------------------------------

def _make_next_data_html(product: dict) -> str:
    payload = json.dumps({"props": {"pageProps": {"product": product}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def test_next_data_tyre_extracted() -> None:
    html = _make_next_data_html({
        "name": "Шина Continental ContiWinterContact 205/55 R16",
        "price": 5490,
        "image": "/images/tyre.jpg",
        "brand": "Continental",
    })
    offers = _offers_from_next_data(html, "https://koleso.ru/product/x/", query_tokens=_q("шины 205/55 R16"))
    assert len(offers) == 1
    assert offers[0].price == Decimal("5490")
    assert "Continental" in offers[0].name


def test_next_data_no_match_returns_empty() -> None:
    html = _make_next_data_html({
        "name": "Шина Bridgestone 225/45 R17",
        "price": 6800,
    })
    offers = _offers_from_next_data(html, "https://koleso.ru/product/y/", query_tokens=_q("шины 205/55 R16"))
    assert offers == []


def test_next_data_price_out_of_range_skipped() -> None:
    html = _make_next_data_html({"name": "Шина 205/55 R16", "price": 3})
    offers = _offers_from_next_data(html, "https://koleso.ru/product/z/", query_tokens=_q("шины 205/55 R16"))
    assert offers == []


def test_next_data_missing_block_returns_empty() -> None:
    html = "<html><body>no next data here</body></html>"
    offers = _offers_from_next_data(html, "https://example.ru/", query_tokens=_q("шины"))
    assert offers == []


# ---------------------------------------------------------------------------
# window.digitalData extraction
# ---------------------------------------------------------------------------

def _make_digital_data_html(product_name: str, price: float) -> str:
    data = json.dumps({
        "product": [{"info": {
            "productName": product_name,
            "price": {"basePrice": price},
        }}]
    })
    return f"<script>window.digitalData = {data};</script>"


def test_digital_data_extracted() -> None:
    html = _make_digital_data_html("Кроссовки Nike Air Force 1 42 размер", 8990)
    offers = _offers_from_digital_data(html, "https://street-beat.ru/product/1/", query_tokens=_q("кроссовки nike 42"))
    assert len(offers) == 1
    assert offers[0].price == Decimal("8990")


def test_digital_data_no_html_returns_empty() -> None:
    offers = _offers_from_digital_data("<html></html>", "https://example.ru/", query_tokens=_q("кроссовки"))
    assert offers == []


# ---------------------------------------------------------------------------
# Microdata extraction
# ---------------------------------------------------------------------------

_MICRODATA_HTML = """
<div itemscope itemtype="https://schema.org/Product">
  <span itemprop="name">Картридж Canon 725 для LBP6000</span>
  <span itemprop="price" content="1250">1 250</span>
  <img itemprop="image" src="/img/canon725.jpg">
  <span itemprop="brand">Canon</span>
  <span itemprop="sku">CRG-725</span>
</div>
"""


def test_microdata_cartridge_extracted() -> None:
    offers = _offers_from_microdata(
        _MICRODATA_HTML, "https://kns.ru/product/canon-725/", query_tokens=_q("картридж Canon 725"),
    )
    assert len(offers) == 1
    assert offers[0].price == Decimal("1250")
    assert "Canon" in offers[0].name
    assert offers[0].characteristics.get("brand") == "Canon"
    assert offers[0].characteristics.get("sku") == "CRG-725"


def test_microdata_no_product_scope_returns_empty() -> None:
    html = "<div><span>Картридж Canon 725</span><span>1250 руб</span></div>"
    offers = _offers_from_microdata(html, "https://example.ru/", query_tokens=_q("картридж Canon 725"))
    assert offers == []


def test_microdata_price_missing_returns_empty() -> None:
    html = '<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Canon 725</span></div>'
    offers = _offers_from_microdata(html, "https://example.ru/", query_tokens=_q("картридж Canon 725"))
    assert offers == []


# ---------------------------------------------------------------------------
# HTML meta/heuristic fallback
# ---------------------------------------------------------------------------

_OG_HTML = """
<html><head>
  <meta property="og:title" content="Кроссовки Nike Air 42 мужские">
  <meta property="og:image" content="https://example.ru/img.jpg">
  <meta property="product:price:amount" content="7500">
</head></html>
"""


def test_html_og_offer_extracted() -> None:
    offer = _html_offer("https://sneakerhead.ru/product/1/", _OG_HTML, query_tokens=_q("кроссовки nike 42"))
    assert offer is not None
    assert offer.price == Decimal("7500")
    assert "Nike" in offer.name


def test_html_offer_query_mismatch_returns_none() -> None:
    offer = _html_offer("https://sneakerhead.ru/product/1/", _OG_HTML, query_tokens=_q("шины 205/55 R16"))
    assert offer is None


# ---------------------------------------------------------------------------
# _name_matches_query edge cases
# ---------------------------------------------------------------------------

def test_name_matches_strong_tokens_required() -> None:
    assert _name_matches_query("iphone 15 128gb черный", _q("iphone 15 128"))
    assert not _name_matches_query("iphone 14 128gb черный", _q("iphone 15 128"))


def test_name_matches_tyre_size_exact() -> None:
    assert _name_matches_query("Шина Nokian 205/55 R16 94T шипованная", _q("шины 205/55 R16 зимние шипованные"))
    assert not _name_matches_query("Шина Nokian 225/45 R17 зимняя", _q("шины 205/55 R16 зимние"))


def test_name_matches_lowered_threshold_when_strong_tokens_present() -> None:
    # "зимние" may not appear in a product title — but if r15 matches, the
    # overlap threshold is relaxed so the offer is not rejected.
    assert _name_matches_query("Шина Nokian 185/65 R15 88T шипованная", _q("шины R15 зимние"))
    # Different rim size must still be rejected even with lowered threshold.
    assert not _name_matches_query("Шина Nokian 185/65 R16 88T шипованная", _q("шины R15 зимние"))


def test_name_matches_no_strong_token_keeps_50pct_threshold() -> None:
    # Without numeric tokens, normal 50% threshold applies.
    assert _name_matches_query("кроссовки мужские черные", _q("кроссовки мужские"))
    assert not _name_matches_query("куртка мужская черная", _q("кроссовки мужские черные"))
