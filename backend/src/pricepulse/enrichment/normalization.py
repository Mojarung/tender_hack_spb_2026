"""Deterministic normalizers and key-alias tables for marketplace characteristics.

Maps Russian/English marketplace spec keys → canonical ProductAttributes fields
and normalises raw string values to typed Python values.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_SPACES = re.compile(r"\s+")

# ---------------------------------------------------------------------------
# Key alias dictionary
# ---------------------------------------------------------------------------

# Maps normalised (lowercased, stripped, NFKC) characteristic key names
# → canonical ProductAttributes field name (or private sentinel starting with _).
CHAR_KEY_ALIASES: dict[str, str] = {
    # Storage / internal memory
    "объем встроенной памяти": "storage_gb",
    "объём встроенной памяти": "storage_gb",
    "встроенная память": "storage_gb",
    "внутренняя память": "storage_gb",
    "встроенная память (rom)": "storage_gb",
    "память": "storage_gb",
    "rom": "storage_gb",
    "storage": "storage_gb",
    "накопитель": "storage_gb",
    "объем ssd": "storage_gb",
    "объём ssd": "storage_gb",
    "емкость накопителя": "storage_gb",
    "ёмкость накопителя": "storage_gb",
    "объём накопителя": "storage_gb",
    "объем накопителя": "storage_gb",
    "объём жесткого диска": "storage_gb",
    "объем жесткого диска": "storage_gb",
    "ssd накопитель": "storage_gb",
    # RAM
    "оперативная память": "ram_gb",
    "объём оперативной памяти": "ram_gb",
    "объем оперативной памяти": "ram_gb",
    "озу": "ram_gb",
    "ram": "ram_gb",
    "оперативная память (ram)": "ram_gb",
    # Color
    "цвет": "color",
    "цвет товара": "color",
    "цвет для фильтра": "color",
    "основной цвет": "color",
    "color": "color",
    "colour": "color",
    # Manufacturer/marketing color (goes to raw, not canonical color)
    "название цвета от производителя": "_manufacturer_color",
    "цвет производителя": "_manufacturer_color",
    "manufacturer color": "_manufacturer_color",
    "цвет по паспорту": "_manufacturer_color",
    # Brand
    "бренд": "brand",
    "производитель": "brand",
    "марка": "brand",
    "торговая марка": "brand",
    "brand": "brand",
    "торговое наименование": "brand",
    # Model
    "модель": "model",
    "модель товара": "model",
    "артикул производителя": "model",
    "mpn": "model",
    "model": "model",
    "наименование модели": "model",
    "серия": "model",
    # Paper
    "формат": "paper_format",
    "формат бумаги": "paper_format",
    "плотность": "density_gm2",
    "плотность бумаги": "density_gm2",
    "плотность, г/м2": "density_gm2",
    "плотность г/м2": "density_gm2",
    "грамматура": "density_gm2",
    "количество листов": "sheets_count",
    "листов в пачке": "sheets_count",
    "количество в упаковке": "sheets_count",
    "листов": "sheets_count",
    # Tyres
    "ширина профиля": "tyre_width_mm",
    "высота профиля": "tyre_profile",
    "посадочный диаметр": "tyre_rim_inch",
    "диаметр диска": "tyre_rim_inch",
    "сезонность": "season",
    "тип сезона": "season",
    "назначение": "_tyre_type",   # "зимние шины", "летние шины"
    "тип шины": "_tyre_type",
    "шипы": "studs",
    "шипованность": "studs",
    "наличие шипов": "studs",
    # Office equipment
    "тип устройства": "device_type",
    "тип": "device_type",
    "технология печати": "print_technology",
    "метод печати": "print_technology",
    "цветная печать": "color_print",
    "wi-fi": "wifi",
    "wifi": "wifi",
    "wi fi": "wifi",
    "беспроводная связь": "wifi",
    "wireless": "wifi",
    # Apparel
    "размер": "size",
    "российский размер": "size",
    "размер одежды": "size",
    "пол": "gender",
    "половозрастная группа": "gender",
    "для кого": "gender",
    "материал": "material",
    "состав": "material",
    "ткань": "material",
    "материал верха": "material",
}

# ---------------------------------------------------------------------------
# Value normalizers
# ---------------------------------------------------------------------------

_MEM_VALUE_RE = re.compile(
    r"(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>тб|tb|гб|gb|мб|mb)",
    flags=re.IGNORECASE,
)


def _nrm(value: str) -> str:
    """Lowercase + NFKC + collapse whitespace."""
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", value).lower()).strip()


def normalize_memory_str_to_gb(value: str) -> int | None:
    """Parse '256 ГБ', '1 ТБ', '1 024 МБ', bare '256' → GB int."""
    match = _MEM_VALUE_RE.search(value)
    if not match:
        bare = re.sub(r"\s", "", value).strip()
        if bare.isdigit():
            n = int(bare)
            return n if n >= 1 else None
        return None
    n_str = match.group("n").replace(",", ".")
    n = float(n_str)
    unit = match.group("u").lower()
    if unit in ("тб", "tb"):
        return int(n * 1024)
    if unit in ("мб", "mb"):
        mb = int(n)
        return mb // 1024 if mb >= 1024 else None
    return int(n)  # гб / gb


_COLOR_MAP: dict[str, str] = {
    # black
    "black": "black", "чёрный": "black", "черный": "black", "чёрная": "black",
    "черная": "black", "чёрные": "black", "черные": "black", "midnight": "black",
    "space black": "black", "onyx black": "black", "phantom black": "black",
    # white
    "white": "white", "белый": "white", "белая": "white", "белые": "white",
    "star white": "white", "ivory": "white", "cream": "white",
    # blue
    "blue": "blue", "синий": "blue", "синяя": "blue", "синие": "blue",
    "голубой": "blue", "navy": "blue", "темно-синий": "blue", "тёмно-синий": "blue",
    "темно-синяя": "blue", "cobalt": "blue", "sky blue": "blue",
    "titanium blue": "blue", "голубая": "blue", "сапфировый": "blue",
    # red
    "red": "red", "красный": "red", "красная": "red", "красные": "red",
    "crimson": "red", "scarlet": "red",
    # pink
    "pink": "pink", "розовый": "pink", "розовая": "pink", "розовые": "pink",
    "rose gold": "pink", "light pink": "pink", "лавандовый": "pink",
    # green
    "green": "green", "зеленый": "green", "зелёный": "green", "зеленая": "green",
    "зелёная": "green", "sage": "green", "forest green": "green", "mint": "green",
    "хаки": "green",
    # yellow
    "yellow": "yellow", "желтый": "yellow", "жёлтый": "yellow", "желтая": "yellow",
    "жёлтая": "yellow",
    # gray / grey
    "gray": "gray", "grey": "gray", "серый": "gray", "серая": "gray", "серые": "gray",
    "space gray": "gray", "graphite": "gray", "titanium": "gray", "slate": "gray",
    "тёмно-серый": "gray", "темно-серый": "gray",
    # silver
    "silver": "silver", "серебристый": "silver", "серебро": "silver",
    "серебристая": "silver", "серебристые": "silver",
    # gold
    "gold": "gold", "золотой": "gold", "золотая": "gold", "champagne": "gold",
    "starlight": "gold", "бежевый": "gold",
    # purple
    "purple": "purple", "violet": "purple", "фиолетовый": "purple",
    "фиолетовая": "purple", "сиреневый": "purple", "сиреневая": "purple",
    # orange
    "orange": "orange", "оранжевый": "orange", "оранжевая": "orange", "coral": "orange",
    # brown
    "brown": "brown", "коричневый": "brown", "коричневая": "brown",
    "коричневые": "brown", "bronze": "brown", "бронзовый": "brown",
}


def normalize_color_str(value: str) -> str | None:
    """Map marketplace color strings to canonical lowercase English values."""
    if not value:
        return None
    normed = _nrm(value).replace("ё", "е")
    # Exact match
    if normed in _COLOR_MAP:
        return _COLOR_MAP[normed]
    # Substring match (handles compound strings like "Midnight Black 256GB")
    for key, canonical in _COLOR_MAP.items():
        if key in normed:
            return canonical
    return None


_BOOL_TRUE = {"да", "yes", "true", "+", "есть", "имеется", "1", "supported", "поддерживается"}
_BOOL_FALSE = {"нет", "no", "false", "-", "отсутствует", "0", "not supported", "нет шипов"}


def normalize_boolean_str(value: str) -> bool | None:
    """Parse localised yes/no strings to Python bool."""
    normed = _nrm(value)
    if normed in _BOOL_TRUE:
        return True
    if normed in _BOOL_FALSE:
        return False
    # Substring check for multi-word values
    if any(w in normed for w in ("есть", "поддерживает")):
        return True
    if any(w in normed for w in ("нет", "отсутств")):
        return False
    return None


def normalize_season_str(value: str) -> str | None:
    """Parse season strings to canonical 'winter' / 'summer' / 'all_season'."""
    normed = _nrm(value)
    if any(w in normed for w in ("зим", "winter")):
        return "winter"
    if any(w in normed for w in ("летн", "лето", "summer")):
        return "summer"
    if any(w in normed for w in ("всесез", "all-season", "all season")):
        return "all_season"
    return None


_DEVICE_TYPE_MAP = {
    "мфу": "mfp", "mfp": "mfp", "многофункциональное": "mfp",
    "принтер": "printer", "printer": "printer",
    "сканер": "scanner", "scanner": "scanner",
    "ламинатор": "laminator", "laminator": "laminator",
    "шредер": "shredder", "уничтожитель": "shredder", "shredder": "shredder",
    "копир": "copier", "copier": "copier",
}


def normalize_device_type_str(value: str) -> str | None:
    normed = _nrm(value)
    for key, canonical in _DEVICE_TYPE_MAP.items():
        if key in normed:
            return canonical
    return None


_PRINT_TECH_MAP = {
    "лазер": "laser", "laser": "laser",
    "струй": "inkjet", "inkjet": "inkjet",
    "светодиод": "led", "led": "led",
}


def normalize_print_tech_str(value: str) -> str | None:
    normed = _nrm(value)
    for key, canonical in _PRINT_TECH_MAP.items():
        if key in normed:
            return canonical
    return None


_GENDER_MAP = {
    "мужской": "men", "мужская": "men", "мужские": "men", "мужчина": "men",
    "men": "men", "male": "men", "mens": "men", "для мужчин": "men",
    "женский": "women", "женская": "women", "женские": "women", "женщина": "women",
    "women": "women", "female": "women", "womens": "women", "для женщин": "women",
    "детский": "kids", "детская": "kids", "детские": "kids",
    "kids": "kids", "children": "kids", "child": "kids", "для детей": "kids",
    "унисекс": "unisex", "unisex": "unisex",
}


def normalize_gender_str(value: str) -> str | None:
    normed = _nrm(value)
    if normed in _GENDER_MAP:
        return _GENDER_MAP[normed]
    for key, canonical in _GENDER_MAP.items():
        if key in normed:
            return canonical
    return None


_SIZE_RE = re.compile(
    r"\b(xs|s|m|l|xl|xxl|xxxl|[2-5]xl|\d{2,3}(?:-\d{2,3})?)\b",
    re.IGNORECASE,
)


def normalize_size_str(value: str) -> str | None:
    match = _SIZE_RE.search(value)
    return match.group(1).upper() if match else None


_PAPER_FORMAT_RE = re.compile(r"\b[aаА]([0-5])\b", re.IGNORECASE)


def normalize_paper_format_str(value: str) -> str | None:
    match = _PAPER_FORMAT_RE.search(value)
    return f"A{match.group(1)}" if match else None


_DENSITY_RE = re.compile(r"(\d{2,3})\s*(?:г\s*/\s*м2|г/м2|г\s*м2|g\s*/\s*m2|gsm|г\.?/м²)?")


def normalize_density_str(value: str) -> int | None:
    match = _DENSITY_RE.search(value)
    if match:
        n = int(match.group(1))
        if 40 <= n <= 350:  # sanity range for paper density g/m²
            return n
    return None


_SHEETS_RE = re.compile(r"(\d{2,5})\s*(?:лист|листов|л\.|sheets?)?")


def normalize_sheets_str(value: str) -> int | None:
    match = _SHEETS_RE.search(value)
    if match:
        n = int(match.group(1))
        if 10 <= n <= 10000:
            return n
    return None


def normalize_tyre_width_str(value: str) -> int | None:
    match = re.search(r"(\d{3})", value)
    if match:
        n = int(match.group(1))
        if 100 <= n <= 400:
            return n
    return None


def normalize_tyre_profile_str(value: str) -> int | None:
    match = re.search(r"(\d{2})", value)
    if match:
        n = int(match.group(1))
        if 20 <= n <= 90:
            return n
    return None


def normalize_tyre_rim_str(value: str) -> int | None:
    match = re.search(r"(\d{2})", value)
    if match:
        n = int(match.group(1))
        if 10 <= n <= 26:
            return n
    return None


# Dispatch table: canonical field name → normalizer function
_FIELD_NORMALIZERS: dict[str, Any] = {
    "storage_gb": normalize_memory_str_to_gb,
    "ram_gb": normalize_memory_str_to_gb,
    "color": normalize_color_str,
    "color_print": normalize_boolean_str,
    "wifi": normalize_boolean_str,
    "studs": normalize_boolean_str,
    "season": normalize_season_str,
    "device_type": normalize_device_type_str,
    "print_technology": normalize_print_tech_str,
    "gender": normalize_gender_str,
    "size": normalize_size_str,
    "paper_format": normalize_paper_format_str,
    "density_gm2": normalize_density_str,
    "sheets_count": normalize_sheets_str,
    "tyre_width_mm": normalize_tyre_width_str,
    "tyre_profile": normalize_tyre_profile_str,
    "tyre_rim_inch": normalize_tyre_rim_str,
}

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def normalize_structured_characteristics(
    characteristics: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Normalise marketplace characteristic key/value pairs.

    Returns:
        (canonical_fields, extra_raw) where:
        - canonical_fields: {field_name: typed_value} ready to populate ProductAttributes
        - extra_raw: {raw_key: raw_value} for non-mapped keys, stored in ProductAttributes.extra
    """
    canonical: dict[str, Any] = {}
    extra_raw: dict[str, str] = {}

    for raw_key, raw_value in characteristics.items():
        raw_value = (raw_value or "").strip()
        if not raw_value:
            continue
        key_normed = _nrm(raw_key).replace("ё", "е")
        field = CHAR_KEY_ALIASES.get(key_normed)

        if field is None:
            extra_raw[raw_key] = raw_value
            continue

        if field == "_manufacturer_color":
            extra_raw["manufacturer_color"] = raw_value
            continue

        if field == "_tyre_type":
            season = normalize_season_str(raw_value)
            if season and "season" not in canonical:
                canonical["season"] = season
            continue

        normalizer = _FIELD_NORMALIZERS.get(field)
        if normalizer is not None:
            result = normalizer(raw_value)
            if result is not None and field not in canonical:
                canonical[field] = result
        else:
            # String fields: brand, model, paper_format (fallback)
            cleaned = raw_value.strip()
            if cleaned and field not in canonical:
                canonical[field] = cleaned.lower() if field in ("brand", "model") else cleaned

    return canonical, extra_raw


__all__ = [
    "CHAR_KEY_ALIASES",
    "normalize_boolean_str",
    "normalize_color_str",
    "normalize_density_str",
    "normalize_device_type_str",
    "normalize_gender_str",
    "normalize_memory_str_to_gb",
    "normalize_paper_format_str",
    "normalize_print_tech_str",
    "normalize_season_str",
    "normalize_sheets_str",
    "normalize_size_str",
    "normalize_structured_characteristics",
    "normalize_tyre_profile_str",
    "normalize_tyre_rim_str",
    "normalize_tyre_width_str",
]
