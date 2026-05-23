from pricepulse.scrapers.yandex_market import (
    _extract_image,
    _parse_specs_from_product_html,
    build_region_cookies,
    build_region_url,
    build_search_url,
)


def test_build_search_url_includes_region_id() -> None:
    url = build_search_url("iphone 15", region_id=2)

    assert url == "https://market.yandex.ru/search?text=iphone+15&lr=2"


def test_build_region_url_adds_region_id_to_offer_url() -> None:
    url = build_region_url("https://market.yandex.ru/product--phone/123?sku=456", region_id=43)

    assert url == "https://market.yandex.ru/product--phone/123?sku=456&lr=43"


def test_build_region_url_overrides_existing_region_id() -> None:
    url = build_region_url(
        "https://market.yandex.ru/product--phone/123?lr=2&sku=456",
        region_id=213,
    )

    assert url == "https://market.yandex.ru/product--phone/123?lr=213&sku=456"


def test_build_region_cookies_include_yandex_gid() -> None:
    assert build_region_cookies(region_id=43)["yandex_gid"] == "43"


def test_extract_image_supports_schema_object() -> None:
    image = _extract_image({"@type": "ImageObject", "contentUrl": "//avatars.mds.yandex.net/x"})

    assert image == "https://avatars.mds.yandex.net/x"


def test_extract_image_supports_nested_list() -> None:
    image = _extract_image([{}, {"thumbnailUrl": "https://avatars.mds.yandex.net/y"}])

    assert image == "https://avatars.mds.yandex.net/y"


_SAMPLE_SPECS_HTML = """\
<div aria-label="Характеристики">
  <div class="zh0TB">
    <div class="_3rW2x _1MOwX _2eMnU">
      <div class="_1IaDe"><div class="_6DaYY">
        <span data-auto="product-spec" class="ds-text">Бренд</span>
      </div></div>
      <div class="eXP5k"><div class="b2ZT4">
        <a href="/category/x"><div class="ds-text"><span>Tuvio</span></div></a>
      </div></div>
    </div>
    <div class="_3rW2x _1MOwX _2eMnU">
      <div class="_1IaDe"><div class="_6DaYY">
        <span data-auto="product-spec" class="ds-text">Цвет товара</span>
      </div></div>
      <div class="eXP5k"><div class="b2ZT4">
        <div class="ds-text"><span>белый</span></div>
      </div></div>
    </div>
    <div class="_3rW2x _1MOwX _2eMnU">
      <div class="_1IaDe"><div class="_6DaYY">
        <span data-auto="product-spec" class="ds-text">Тип</span>
      </div></div>
      <div class="eXP5k"><div class="b2ZT4">
        <div class="ds-text"><span>измельчитель</span></div>
      </div></div>
    </div>
  </div>
</div>
"""


def test_parse_specs_from_product_html_extracts_all_fields() -> None:
    specs = _parse_specs_from_product_html(_SAMPLE_SPECS_HTML)

    assert specs["Бренд"] == "Tuvio"
    assert specs["Цвет товара"] == "белый"
    assert specs["Тип"] == "измельчитель"


def test_parse_specs_from_product_html_returns_empty_without_marker() -> None:
    assert _parse_specs_from_product_html("<html><body>no specs here</body></html>") == {}
