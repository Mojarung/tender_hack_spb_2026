"""Standalone test for _parse_specs_from_product_html — no package install needed."""
import html as html_lib
import re

_SPEC_SPLIT_RE = re.compile(r'data-auto="product-spec"')


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", html_lib.unescape(value)).strip()


def _parse_specs_from_product_html(html: str) -> dict[str, str]:
    result: dict[str, str] = {}
    idx = html.find('aria-label="Характеристики"')
    if idx == -1:
        return result
    chars_html = html[idx : idx + 200_000]
    for seg in _SPEC_SPLIT_RE.split(chars_html)[1:]:
        nm = re.match(r'[^>]*>([^<]+)</span>', seg[:500])
        if not nm:
            continue
        name = _clean_text(html_lib.unescape(nm.group(1)))
        if not name:
            continue
        vm = re.search(r'class="b2ZT4"[^>]*>(.*?)</div>', seg[:3000], re.DOTALL)
        if not vm:
            continue
        value = _clean_text(html_lib.unescape(re.sub(r'<[^>]+>', ' ', vm.group(1))))
        if value:
            result[name] = value
    return result


_SAMPLE = """
<div aria-label="Характеристики">
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
"""

specs = _parse_specs_from_product_html(_SAMPLE)
print("Распознанные характеристики:", specs)

assert specs.get("Бренд") == "Tuvio", f"Бренд: {specs.get('Бренд')!r}"
assert specs.get("Цвет товара") == "белый", f"Цвет товара: {specs.get('Цвет товара')!r}"
assert specs.get("Тип") == "измельчитель", f"Тип: {specs.get('Тип')!r}"

empty = _parse_specs_from_product_html("<html>no specs</html>")
assert empty == {}, f"Ожидали пустой dict, получили: {empty!r}"

print("OK — все 4 проверки прошли")
