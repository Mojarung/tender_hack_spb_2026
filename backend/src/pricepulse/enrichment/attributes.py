"""Rule-based product attribute extraction and matching.

This module is intentionally deterministic and lightweight. Local LLMs can be
added as a fallback later, but the default path must work without model files.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from pricepulse.domain.models import ProductAttributes, ProductOffer
from pricepulse.enrichment.normalization import normalize_structured_characteristics

_PUNCT = re.compile(r"[^\w\s/+.-]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")

_BRANDS: dict[str, str] = {
    "apple": "apple",
    "iphone": "apple",
    "айфон": "apple",
    "айфона": "apple",
    "macbook": "apple",
    "макбук": "apple",
    "airpods": "apple",
    "samsung": "samsung",
    "самсунг": "samsung",
    "galaxy": "samsung",
    "xiaomi": "xiaomi",
    "сяоми": "xiaomi",
    "redmi": "xiaomi",
    "huawei": "huawei",
    "хуавей": "huawei",
    "honor": "honor",
    "hp": "hp",
    "canon": "canon",
    "epson": "epson",
    "brother": "brother",
    "xerox": "xerox",
    "kyocera": "kyocera",
    "pantum": "pantum",
    "sony": "sony",
    "сони": "sony",
    "jbl": "jbl",
    "marshall": "marshall",
    "logitech": "logitech",
    "roborock": "roborock",
    "dyson": "dyson",
    "delonghi": "delonghi",
    "nike": "nike",
    "найк": "nike",
    "adidas": "adidas",
    "адидас": "adidas",
    "puma": "puma",
    "пума": "puma",
    "asus": "asus",
    "lenovo": "lenovo",
    "acer": "acer",
    "msi": "msi",
    "dell": "dell",
    "lg": "lg",
    "philips": "philips",
}

_COLORS: dict[str, str] = {
    "black": "black", "черный": "black", "черная": "black", "черное": "black",
    "черные": "black", "чёрный": "black", "чёрная": "black", "чёрные": "black",
    "midnight": "black",
    "white": "white", "белый": "white", "белая": "white", "белое": "white",
    "белые": "white",
    "blue": "blue", "синий": "blue", "синяя": "blue", "синие": "blue",
    "темно-синий": "blue", "темно-синяя": "blue", "тёмно-синий": "blue",
    "тёмно-синяя": "blue", "голубой": "blue", "navy": "blue",
    "red": "red", "красный": "red", "красная": "red",
    "pink": "pink", "розовый": "pink", "розовая": "pink",
    "green": "green", "зеленый": "green", "зеленая": "green",
    "yellow": "yellow", "желтый": "yellow",
    "gray": "gray", "grey": "gray", "серый": "gray", "серая": "gray",
    "серые": "gray",
    "серебристый": "silver", "silver": "silver",
    "gold": "gold", "золотой": "gold",
    "purple": "purple", "фиолетовый": "purple",
    "orange": "orange", "оранжевый": "orange",
    "brown": "brown", "коричневый": "brown", "коричневая": "brown",
    "коричневые": "brown",
}

_ACCESSORY_WORDS = {
    "чехол", "case", "стекло", "glass", "кабель", "cable", "зарядка",
    "адаптер", "adapter", "держатель", "пленка", "плёнка",
}

_APPAREL_WORDS = {
    "футболка", "майка", "худи", "толстовка", "куртка", "пуховик", "брюки",
    "джинсы", "рубашка", "платье", "носки", "костюм", "юбка",
}

_HEADPHONE_WORDS = {"наушники", "airpods", "headphones", "гарнитура"}
_VACUUM_WORDS = {"пылесос", "робот", "vacuum"}
_PAPER_WORDS = {"бумага", "листов", "пачка", "пачки"}
_CARTRIDGE_WORDS = {"картридж", "картриджи", "тонер"}
_MONITOR_WORDS = {"монитор", "monitor", "display"}
_STORAGE_WORDS = {"ssd", "hdd", "флешка", "накопитель", "usb"}
_INPUT_DEVICE_WORDS = {"клавиатура", "keyboard", "мышь", "мышка", "mouse"}
_PROJECTOR_WORDS = {"проектор", "projector"}
_OFFICE_MISC_WORDS = {"ламинатор", "шредер", "уничтожитель"}
_GLOVES_WORDS = {"перчатки", "gloves"}
_COFFEE_WORDS = {"кофемашина", "кофеварка", "coffee"}

_NOISY_CHARACTERISTIC_KEYS = {
    "feedbacks", "rating", "rating_count", "reviews", "seller", "site", "stock",
    "supplier", "supplier_rating", "subject_id", "subject_parent_id",
    "warehouse_id", "distance_marketplace", "eta_min_hours", "eta_max_hours",
    "weight", "volume",
}

_MATERIALS: dict[str, str] = {
    "хлопок": "cotton", "cotton": "cotton",
    "полиэстер": "polyester", "polyester": "polyester",
    "шерсть": "wool", "wool": "wool",
    "кожа": "leather", "leather": "leather",
    "флис": "fleece",
}

_TYRE_RE = re.compile(r"\b(?P<w>\d{3})\s*/\s*(?P<p>\d{2})\s*r?\s*(?P<r>\d{2})\b")
_TYRE_SPACED_RE = re.compile(r"\b(?P<w>\d{3})\s+(?P<p>\d{2})\s+(?P<r>\d{2})\b")
_IPHONE_RE = re.compile(
    r"\b(?:iphone|айфон)\s*(?P<num>\d{1,2})"
    r"(?:\s*(?P<tier>pro\s*max|про\s*макс|pro|про|max|макс|plus|плюс))?\b"
)
_SAMSUNG_RE = re.compile(
    r"\b(?:samsung|самсунг|galaxy)\s*"
    r"(?P<model>s\d{2}(?:\s*ultra|\s*plus|\s*fe)?)\b"
)
_REDMI_RE = re.compile(r"\b(?:xiaomi\s*)?redmi\s+(?P<model>note\s+\d{1,2}\w*)\b")
_MACBOOK_RE = re.compile(
    r"\b(?:macbook|макбук)\s*(?P<line>air|эйр|эир|pro|про)?\s*(?P<chip>[mм]\d)?\b"
)
_AIRPODS_RE = re.compile(r"\bairpods\s*(?P<tier>pro|max)?\s*(?P<gen>\d)?\b")
_SONY_WH_RE = re.compile(r"\bsony\s+(?P<model>wh[-\s]?\d{4}xm\d)\b")
_MEM_RE = re.compile(r"\b(?P<n>\d{1,4})\s*(?P<u>tb|тб|gb|гб)\b")
_SPLIT_MEM_RE = re.compile(r"\b(?P<ram>\d{1,2})\s*/\s*(?P<storage>\d{2,4})\b")
_SIZE_RE = re.compile(
    r"\b(?P<size>xs|s|m|l|xl|xxl|xxxl|[2-5]xl|\d{2,3}(?:-\d{2,3})?)\b"
)
_PAPER_FORMAT_RE = re.compile(r"\b[аa](?P<n>[0-5])\b")
_DENSITY_RE = re.compile(r"\b(?P<n>\d{2,3})\s*(?:г\s*/\s*м2|g\s*/\s*m2|gsm)\b")
_SHEETS_RE = re.compile(r"\b(?P<n>\d{2,4})\s*(?:лист|листов|л\.)\b")


def _clean(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower().replace("ё", "е")
    value = _PUNCT.sub(" ", value)
    return _SPACES.sub(" ", value).strip()


def _tokens(text: str) -> set[str]:
    return set(_clean(text).split())


def _first_token_match(tokens: set[str], mapping: dict[str, str]) -> str | None:
    for token, canonical in mapping.items():
        if token in tokens:
            return canonical
    return None


def _extract_color(cleaned: str, tokens: set[str]) -> str | None:
    direct = _first_token_match(tokens, _COLORS)
    if direct:
        return direct
    for marker, canonical in _COLORS.items():
        if " " in marker or "-" in marker:
            if marker in cleaned:
                return canonical
    return None


def _attrs_from_characteristics(characteristics: dict[str, Any] | None) -> dict[str, str]:
    if not characteristics:
        return {}
    return {
        str(k).strip(): str(v).strip()
        for k, v in characteristics.items()
        if v not in (None, "") and str(k).strip().lower() not in _NOISY_CHARACTERISTIC_KEYS
    }


def _text_blob(text: str, characteristics: dict[str, str]) -> str:
    parts = [text]
    for key, value in characteristics.items():
        parts.append(f"{key} {value}")
    return " ".join(parts)


def _detect_category(cleaned: str, tokens: set[str]) -> str | None:
    if tokens & _ACCESSORY_WORDS:
        return "accessory"
    if "колесо в сборе" in cleaned or "колеса в сборе" in cleaned:
        return "wheel_assembly"
    if _TYRE_RE.search(cleaned) or tokens & {"шина", "шины", "резина", "покрышка"}:
        return "tyre"
    if tokens & {"принтер", "мфу", "сканер", "копир", "плоттер"}:
        return "office_equipment"
    if tokens & _APPAREL_WORDS:
        return "apparel"
    if tokens & _CARTRIDGE_WORDS:
        return "cartridge"
    if tokens & _PAPER_WORDS:
        return "paper"
    if tokens & _MONITOR_WORDS:
        return "monitor"
    if tokens & _STORAGE_WORDS:
        return "storage"
    if tokens & _INPUT_DEVICE_WORDS:
        return "input_device"
    if tokens & _PROJECTOR_WORDS:
        return "projector"
    if tokens & _OFFICE_MISC_WORDS:
        return "office_equipment"
    if tokens & _GLOVES_WORDS:
        return "apparel"
    if tokens & _COFFEE_WORDS:
        return "coffee_machine"
    if tokens & _HEADPHONE_WORDS:
        return "headphones"
    if "робот-пылесос" in cleaned or "robot-vacuum" in cleaned:
        return "robot_vacuum"
    if tokens & _VACUUM_WORDS and (
        "робот" in tokens or "robot" in tokens or "робот-пылесос" in cleaned
    ):
        return "robot_vacuum"
    if "пылесос" in tokens or "vacuum" in tokens:
        return "vacuum_cleaner"
    if _IPHONE_RE.search(cleaned) or tokens & {"смартфон", "smartphone", "телефон"}:
        return "smartphone"
    if tokens & {"ноутбук", "laptop", "macbook"}:
        return "laptop"
    return None


def _extract_model(cleaned: str) -> str | None:
    iphone = _IPHONE_RE.search(cleaned)
    if iphone:
        tier = iphone.group("tier") or ""
        tier = _SPACES.sub(" ", tier).strip()
        tier = {
            "про": "pro",
            "макс": "max",
            "про макс": "pro max",
            "плюс": "plus",
        }.get(tier, tier)
        return f"iphone {iphone.group('num')}{(' ' + tier) if tier else ''}"
    samsung = _SAMSUNG_RE.search(cleaned)
    if samsung:
        return f"galaxy {_SPACES.sub(' ', samsung.group('model')).strip()}"
    redmi = _REDMI_RE.search(cleaned)
    if redmi:
        return f"redmi {_SPACES.sub(' ', redmi.group('model')).strip()}"
    macbook = _MACBOOK_RE.search(cleaned)
    if macbook:
        line = macbook.group("line") or ""
        chip = (macbook.group("chip") or "").replace("м", "m")
        line = {"эйр": "air", "эир": "air", "про": "pro"}.get(line, line)
        parts = ["macbook", line, chip]
        return " ".join(p for p in parts if p)
    airpods = _AIRPODS_RE.search(cleaned)
    if airpods:
        parts = ["airpods", airpods.group("tier") or "", airpods.group("gen") or ""]
        return " ".join(p for p in parts if p)
    sony = _SONY_WH_RE.search(cleaned)
    if sony:
        return sony.group("model").replace(" ", "-")
    if "playstation 5" in cleaned or "ps5" in cleaned:
        return "playstation 5"
    return None


def _extract_storage_gb(cleaned: str) -> int | None:
    split = _SPLIT_MEM_RE.search(cleaned)
    if split:
        return int(split.group("storage"))
    matches = list(_MEM_RE.finditer(cleaned))
    if not matches:
        return None
    for match in matches:
        n = int(match.group("n"))
        unit = match.group("u")
        if unit in {"tb", "тб"}:
            return n * 1024
        if n >= 16:
            return n
    return None


def _extract_ram_gb(cleaned: str) -> int | None:
    split = _SPLIT_MEM_RE.search(cleaned)
    if split:
        return int(split.group("ram"))
    return None


def _infer_laptop_memory(cleaned: str) -> tuple[int | None, int | None]:
    numbers = [int(n) for n in re.findall(r"\b\d{2,4}\b", cleaned)]
    storage = next((n for n in reversed(numbers) if n in {128, 256, 512, 1024, 2048}), None)
    ram = next((n for n in numbers if n in {8, 16, 18, 24, 32, 36, 48, 64}), None)
    return ram, storage


def _infer_phone_memory(cleaned: str) -> tuple[int | None, int | None]:
    numbers = [int(n) for n in re.findall(r"\b\d{1,4}\b", cleaned)]
    storage = next((n for n in reversed(numbers) if n in {32, 64, 128, 256, 512, 1024}), None)
    ram = next((n for n in numbers if n in {4, 6, 8, 12, 16, 18, 24}), None)
    return ram, storage


def _extract_tyre(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    match = _TYRE_RE.search(cleaned) or _TYRE_SPACED_RE.search(cleaned)
    if match:
        out["tyre_width_mm"] = int(match.group("w"))
        out["tyre_profile"] = int(match.group("p"))
        out["tyre_rim_inch"] = int(match.group("r"))
    if any(word in cleaned for word in ("зим", "winter")):
        out["season"] = "winter"
    elif any(word in cleaned for word in ("летн", "summer")):
        out["season"] = "summer"
    elif any(word in cleaned for word in ("всесез", "all season", "all-season")):
        out["season"] = "all_season"
    if any(word in cleaned for word in ("липуч", "нешип", "фрикцион", "non-studded")):
        out["studs"] = False
    elif any(word in cleaned for word in ("шип", "studded")):
        out["studs"] = True
    return out


def _extract_office(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "мфу" in cleaned:
        out["device_type"] = "mfp"
    elif "принтер" in cleaned:
        out["device_type"] = "printer"
    elif "сканер" in cleaned:
        out["device_type"] = "scanner"
    elif "ламинатор" in cleaned:
        out["device_type"] = "laminator"
    elif "шредер" in cleaned or "уничтожитель" in cleaned:
        out["device_type"] = "shredder"
    if "лазер" in cleaned or "laser" in cleaned:
        out["print_technology"] = "laser"
    elif "струй" in cleaned or "inkjet" in cleaned:
        out["print_technology"] = "inkjet"
    if "цветн" in cleaned or "color" in cleaned:
        out["color_print"] = True
    elif "монохром" in cleaned or "черно-бел" in cleaned or "ч/б" in cleaned:
        out["color_print"] = False
    if "wifi" in cleaned or "wi-fi" in cleaned or "вайфай" in cleaned:
        out["wifi"] = True
    return out


def _extract_apparel(cleaned: str, tokens: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if tokens & {"мужская", "мужской", "мужские", "men", "mens"}:
        out["gender"] = "men"
    elif tokens & {"женская", "женский", "женские", "women", "womens"}:
        out["gender"] = "women"
    elif tokens & {"детская", "детский", "детские", "kids"}:
        out["gender"] = "kids"
    material = _first_token_match(tokens, _MATERIALS)
    if material:
        out["material"] = material
    size = _SIZE_RE.search(cleaned)
    if size:
        out["size"] = size.group("size").upper()
    return out


def _extract_paper(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    fmt = _PAPER_FORMAT_RE.search(cleaned)
    if fmt:
        out["paper_format"] = f"A{fmt.group('n')}"
    density = _DENSITY_RE.search(cleaned)
    if density:
        out["density_gm2"] = int(density.group("n"))
    sheets = _SHEETS_RE.search(cleaned)
    if sheets:
        out["sheets_count"] = int(sheets.group("n"))
    return out


def _extract_cartridge(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    match = re.search(r"\b(?:hp|canon|xerox|brother|kyocera|pantum)\s+[\w-]+\b", cleaned)
    if match:
        out["model"] = match.group(0)
    return out


def _confidence(values: dict[str, Any]) -> float:
    keys = [
        "category", "brand", "model", "color", "storage_gb", "tyre_width_mm",
        "season", "device_type", "print_technology", "size", "gender", "material",
        "paper_format", "density_gm2", "sheets_count",
    ]
    filled = sum(1 for key in keys if values.get(key) not in (None, ""))
    return min(0.95, round(0.2 + filled * 0.13, 2)) if filled else 0.0


def extract_attributes(
    text: str,
    characteristics: dict[str, Any] | None = None,
) -> ProductAttributes:
    """Extract canonical attributes from a query or an offer title.

    Structured marketplace specs win over title parsing; raw chars also go to
    .raw for debugging. Title parsing fills the gaps.
    """
    raw = _attrs_from_characteristics(characteristics)
    blob = _text_blob(text, raw)
    cleaned = _clean(blob)
    tokens = _tokens(cleaned)

    structured: dict[str, Any] = {}
    extra_raw: dict[str, str] = {}
    has_structured = False
    if characteristics:
        structured, extra_raw = normalize_structured_characteristics(characteristics)
        has_structured = bool(structured)

    values: dict[str, Any] = {
        "category": _detect_category(cleaned, tokens),
        "brand": _first_token_match(tokens, _BRANDS),
        "model": _extract_model(cleaned),
        "color": _extract_color(cleaned, tokens),
        "storage_gb": _extract_storage_gb(cleaned),
        "ram_gb": _extract_ram_gb(cleaned),
        "raw": {**raw, **extra_raw},
    }
    if values["model"] and values["model"].startswith("iphone"):
        values["brand"] = values["brand"] or "apple"
        if values["category"] is None:
            values["category"] = "smartphone"
    if values["model"] and values["model"].startswith(("macbook", "airpods")):
        values["brand"] = values["brand"] or "apple"
    if values["model"] and values["model"].startswith("galaxy"):
        values["brand"] = values["brand"] or "samsung"
        values["category"] = values["category"] or "smartphone"
    if values["model"] and values["model"].startswith("redmi"):
        values["brand"] = values["brand"] or "xiaomi"
        values["category"] = values["category"] or "smartphone"

    structured_priority = {
        "storage_gb", "ram_gb", "color", "brand", "season", "studs",
        "wifi", "color_print", "print_technology", "device_type",
        "density_gm2", "sheets_count", "paper_format",
        "tyre_width_mm", "tyre_profile", "tyre_rim_inch",
        "gender", "material", "size",
    }
    for field, val in structured.items():
        if field in structured_priority or values.get(field) in (None, ""):
            values[field] = val

    category = values.get("category")
    if category == "smartphone":
        ram, storage = _infer_phone_memory(cleaned)
        values["ram_gb"] = values.get("ram_gb") or ram
        values["storage_gb"] = values.get("storage_gb") or storage
    if values.get("model") and values["model"].startswith("macbook"):
        values["category"] = category or "laptop"
        ram, storage = _infer_laptop_memory(cleaned)
        values["ram_gb"] = values.get("ram_gb") or ram
        values["storage_gb"] = values.get("storage_gb") or storage
    if values.get("model") and values["model"].startswith("airpods"):
        values["category"] = values.get("category") or "headphones"

    category = values.get("category")
    if category == "tyre":
        tyre = _extract_tyre(cleaned)
        for k, v in tyre.items():
            values.setdefault(k, v)
        for field in ("tyre_width_mm", "tyre_profile", "tyre_rim_inch", "season", "studs"):
            if field in structured:
                values[field] = structured[field]
    if category == "office_equipment":
        office = _extract_office(cleaned)
        for k, v in office.items():
            values.setdefault(k, v)
        for field in ("device_type", "print_technology", "color_print", "wifi"):
            if field in structured:
                values[field] = structured[field]
    if category == "apparel":
        apparel = _extract_apparel(cleaned, tokens)
        for k, v in apparel.items():
            values.setdefault(k, v)
        for field in ("size", "gender", "material"):
            if field in structured:
                values[field] = structured[field]
    if category == "paper":
        paper = _extract_paper(cleaned)
        for k, v in paper.items():
            values.setdefault(k, v)
        for field in ("paper_format", "density_gm2", "sheets_count"):
            if field in structured:
                values[field] = structured[field]
    if category == "cartridge":
        values.update(_extract_cartridge(cleaned))

    base_confidence = _confidence(values)
    if has_structured:
        values["confidence"] = max(base_confidence, min(0.95, base_confidence + 0.15))
    else:
        values["confidence"] = base_confidence

    return ProductAttributes(**values)


def extract_query_attributes(text: str) -> ProductAttributes:
    return extract_attributes(text)


def extract_offer_attributes(offer: ProductOffer) -> ProductAttributes:
    return extract_attributes(offer.name, offer.characteristics)


def merge_attributes(
    primary: ProductAttributes | None,
    fallback: ProductAttributes,
) -> ProductAttributes:
    """Fill missing values in `primary` from `fallback`; primary wins."""
    if primary is None:
        return fallback
    data = fallback.model_dump()
    primary_data = primary.model_dump()
    for key, value in primary_data.items():
        if value in (None, "", {}):
            continue
        if key == "confidence" and value == 0.0:
            continue
        data[key] = value
    data["confidence"] = max(primary.confidence, fallback.confidence)
    data["raw"] = {**fallback.raw, **primary.raw}
    data["extra"] = {**fallback.extra, **primary.extra}
    return ProductAttributes(**data)


def _field_names(attrs: ProductAttributes) -> Iterable[str]:
    category = attrs.category
    common = ["category", "brand", "model", "color", "storage_gb"]
    if category == "tyre":
        return [
            "category", "brand", "tyre_width_mm", "tyre_profile",
            "tyre_rim_inch", "season", "studs",
        ]
    if category == "office_equipment":
        return ["category", "brand", "device_type", "print_technology", "color_print", "wifi"]
    if category == "apparel":
        return ["category", "brand", "color", "size", "gender", "material"]
    if category == "paper":
        return ["category", "paper_format", "density_gm2", "sheets_count"]
    if category == "cartridge":
        return ["category", "brand", "model"]
    if category in {"headphones", "laptop", "robot_vacuum"}:
        return ["category", "brand", "model", "color", "storage_gb", "ram_gb"]
    return common


def _weight(field: str) -> float:
    return {
        "category": 2.0, "brand": 2.0, "model": 3.0, "color": 1.0,
        "storage_gb": 1.5, "ram_gb": 1.0,
        "tyre_width_mm": 2.0, "tyre_profile": 2.0, "tyre_rim_inch": 2.0,
        "season": 1.5, "studs": 1.0,
        "device_type": 2.0, "print_technology": 1.5, "color_print": 1.0, "wifi": 0.7,
        "size": 1.5, "gender": 0.7, "material": 0.7,
        "paper_format": 1.5, "density_gm2": 1.0, "sheets_count": 1.0,
    }.get(field, 1.0)


def attribute_match_score(
    query: ProductAttributes | None,
    offer: ProductAttributes | None,
) -> float:
    """Return 0..1 relevance score for canonical query attributes vs offer."""
    if query is None or query.confidence <= 0:
        return 0.5
    if offer is None or offer.confidence <= 0:
        return 0.35
    if query.category and offer.category and query.category != offer.category:
        return 0.0

    total = 0.0
    matched = 0.0
    for field in _field_names(query):
        qv = getattr(query, field)
        if qv in (None, ""):
            continue
        weight = _weight(field)
        total += weight
        ov = getattr(offer, field)
        if ov in (None, ""):
            matched += weight * 0.45
        elif ov == qv:
            matched += weight
    if total == 0:
        return 0.5
    return round(matched / total, 4)


# --- Conflict detection and explainable breakdown ----------------------------

_HARD_FIELDS = ("category", "brand", "model")
_SOFT_MISMATCH_FIELDS = {"color", "storage_gb", "ram_gb", "season", "size"}
_CONFLICT_CONF_MIN = 0.55


def _tokenize_model(value: str) -> list[str]:
    return [t for t in re.split(r"[\s\-_/]+", value.strip().lower()) if t]


def _model_prefix_compatible(a: str, b: str) -> bool:
    """True if one model string is a token-wise prefix of the other.

    "iphone 15" vs "iphone 15 pro" → True
    "iphone 15" vs "iphone 14" → False
    """
    ta = _tokenize_model(a)
    tb = _tokenize_model(b)
    if not ta or not tb:
        return True
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return long_[: len(short)] == short


def is_attribute_conflict(
    query: ProductAttributes | None,
    offer: ProductAttributes | None,
) -> tuple[bool, list[str]]:
    """True if there's a CONFIRMED hard mismatch between query and offer.

    Both sides must have the field populated AND confidence >= 0.55.
    Model has prefix-compatibility allowance.
    """
    if query is None or offer is None:
        return False, []
    if query.confidence < _CONFLICT_CONF_MIN or offer.confidence < _CONFLICT_CONF_MIN:
        return False, []
    reasons: list[str] = []
    for field in _HARD_FIELDS:
        qv = getattr(query, field, None)
        ov = getattr(offer, field, None)
        if not qv or not ov:
            continue
        if field == "model":
            if not _model_prefix_compatible(str(qv), str(ov)):
                reasons.append(field)
        elif qv != ov:
            reasons.append(field)
    return bool(reasons), reasons


def relevance_breakdown(
    query: ProductAttributes | None,
    offer: ProductAttributes | None,
) -> dict[str, Any]:
    """Transparent relevance score with per-field signals for UI explain."""
    if query is None or query.confidence <= 0:
        return {"score": 0.5, "matched": [], "mismatched": [], "unknown": []}
    if offer is None or offer.confidence <= 0:
        return {"score": 0.35, "matched": [], "mismatched": [], "unknown": []}
    if query.category and offer.category and query.category != offer.category:
        return {"score": 0.0, "matched": [], "mismatched": ["category"], "unknown": []}

    matched: list[str] = []
    mismatched: list[str] = []
    unknown: list[str] = []
    total = 0.0
    accrued = 0.0

    for field in _field_names(query):
        qv = getattr(query, field, None)
        if qv in (None, ""):
            continue
        weight = _weight(field)
        total += weight
        ov = getattr(offer, field, None)
        if ov in (None, ""):
            unknown.append(field)
            accrued += weight * 0.45
            continue
        if ov == qv:
            matched.append(field)
            accrued += weight
        elif field == "model" and _model_prefix_compatible(str(qv), str(ov)):
            # Prefix match: offer model has extra variant tokens (e.g. "iphone 15 pro"
            # vs query "iphone 15"). Compatible but not exact — partial credit so
            # the exact variant ranks above the extended one.
            ta = _tokenize_model(str(qv))
            tb = _tokenize_model(str(ov))
            credit = 0.8 if len(tb) > len(ta) else 1.0
            matched.append(field)
            accrued += weight * credit
        elif field in _SOFT_MISMATCH_FIELDS:
            mismatched.append(field)
            accrued += weight * 0.10
        else:
            mismatched.append(field)

    score = round(accrued / total, 4) if total else 0.5
    return {"score": score, "matched": matched, "mismatched": mismatched, "unknown": unknown}


__all__ = [
    "attribute_match_score",
    "extract_attributes",
    "extract_offer_attributes",
    "extract_query_attributes",
    "is_attribute_conflict",
    "merge_attributes",
    "relevance_breakdown",
]
