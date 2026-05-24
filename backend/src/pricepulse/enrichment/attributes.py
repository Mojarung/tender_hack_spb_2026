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
    # Apple
    "apple": "apple",
    "iphone": "apple",
    "айфон": "apple",
    "айфона": "apple",
    "macbook": "apple",
    "макбук": "apple",
    "airpods": "apple",
    "ipad": "apple",
    "айпад": "apple",
    "imac": "apple",
    # Samsung
    "samsung": "samsung",
    "самсунг": "samsung",
    "galaxy": "samsung",
    # Xiaomi / POCO / Redmi
    "xiaomi": "xiaomi",
    "сяоми": "xiaomi",
    "redmi": "xiaomi",
    "poco": "poco",
    "realme": "realme",
    "oppo": "oppo",
    "oneplus": "oneplus",
    "vivo": "vivo",
    # Huawei / Honor
    "huawei": "huawei",
    "хуавей": "huawei",
    "honor": "honor",
    # HP / Dell / Acer / Asus / Lenovo / MSI
    "hp": "hp",
    "asus": "asus",
    "lenovo": "lenovo",
    "acer": "acer",
    "msi": "msi",
    "dell": "dell",
    # Printers / MFP
    "canon": "canon",
    "epson": "epson",
    "brother": "brother",
    "xerox": "xerox",
    "kyocera": "kyocera",
    "pantum": "pantum",
    "ricoh": "ricoh",
    "konica": "konica",
    # Audio / Gaming
    "sony": "sony",
    "сони": "sony",
    "jbl": "jbl",
    "marshall": "marshall",
    "logitech": "logitech",
    "bose": "bose",
    "sennheiser": "sennheiser",
    "зеннхайзер": "sennheiser",
    "jabra": "jabra",
    "hyperx": "hyperx",
    "razer": "razer",
    "steelseries": "steelseries",
    # Vacuum / Cleaning
    "roborock": "roborock",
    "dyson": "dyson",
    "karcher": "karcher",
    "кёрхер": "karcher",
    "кархер": "karcher",
    "irobot": "irobot",
    "ecovacs": "ecovacs",
    # Coffee
    "delonghi": "delonghi",
    "jura": "jura",
    "philips": "philips",
    # Displays / TV
    "lg": "lg",
    "hisense": "hisense",
    "хисенс": "hisense",
    "tcl": "tcl",
    "grundig": "grundig",
    "haier": "haier",
    "хайер": "haier",
    # Footwear / Apparel
    "nike": "nike",
    "найк": "nike",
    "adidas": "adidas",
    "адидас": "adidas",
    "puma": "puma",
    "пума": "puma",
    "reebok": "reebok",
    "рибок": "reebok",
    "new balance": "new balance",
    "columbia": "columbia",
    "converse": "converse",
    # Large appliances
    "bosch": "bosch",
    "бош": "bosch",
    "siemens": "siemens",
    "сименс": "siemens",
    "indesit": "indesit",
    "индезит": "indesit",
    "beko": "beko",
    "беко": "beko",
    "gorenje": "gorenje",
    "горенье": "gorenje",
    "electrolux": "electrolux",
    "электролюкс": "electrolux",
    "whirlpool": "whirlpool",
    "вирпул": "whirlpool",
    "ariston": "ariston",
    "аристон": "ariston",
    "atlant": "atlant",
    "атлант": "atlant",
    "liebherr": "liebherr",
    "либхерр": "liebherr",
    "miele": "miele",
    "candy": "candy",
    "zanussi": "zanussi",
    "hotpoint": "hotpoint",
    "hansa": "hansa",
    "midea": "midea",
    # Power tools
    "makita": "makita",
    "макита": "makita",
    "dewalt": "dewalt",
    "metabo": "metabo",
    "метабо": "metabo",
    "stanley": "stanley",
    "ryobi": "ryobi",
    "интерскол": "interskol",
    "interskol": "interskol",
    "elitech": "elitech",
    "element": "element",
    "patriot": "patriot",
    "зубр": "zubr",
    # Networking
    "tp-link": "tp-link",
    "tplink": "tp-link",
    "тп-линк": "tp-link",
    "netgear": "netgear",
    "zyxel": "zyxel",
    "зиксель": "zyxel",
    "mikrotik": "mikrotik",
    "микротик": "mikrotik",
    "ubiquiti": "ubiquiti",
    "d-link": "d-link",
    "dlink": "d-link",
    # UPS
    "apc": "apc",
    "powercom": "powercom",
    "ippon": "ippon",
    "eaton": "eaton",
    "schneider": "schneider",
    # Cameras
    "nikon": "nikon",
    "никон": "nikon",
    "fujifilm": "fujifilm",
    "fujinon": "fujifilm",
    "olympus": "olympus",
    "олимпус": "olympus",
    "gopro": "gopro",
    "dji": "dji",
    # Misc tech
    "amazon": "amazon",
    "kindle": "amazon",
    "garmin": "garmin",
    "fitbit": "fitbit",
    "beats": "beats",
    # Furniture
    "ikea": "ikea",
    "икеа": "ikea",
    "hoff": "hoff",
    "хофф": "hoff",
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
_APPAREL_TYPE_MAP: dict[str, str] = {
    "футболка": "t_shirt",
    "майка": "t_shirt",
    "худи": "hoodie",
    "толстовка": "hoodie",
    "куртка": "jacket",
    "пуховик": "down_jacket",
    "брюки": "pants",
    "штаны": "pants",
    "джинсы": "jeans",
    "рубашка": "shirt",
    "платье": "dress",
    "носки": "socks",
    "костюм": "suit",
    "юбка": "skirt",
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
_OFFICE_SUPPLY_WORDS = {
    "степлер", "степлеры", "дырокол", "дыроколы", "скобы", "скоба", "скрепки", "скрепка",
    "папка", "папки", "файл", "файлы", "зажим", "зажимы", "ручка", "ручки", "маркер",
    "маркеры", "карандаш", "карандаши", "клей", "ножницы", "корректор", "ластик", "линейка",
}
_GLOVES_WORDS = {"перчатки", "gloves"}
_COFFEE_WORDS = {"кофемашина", "кофеварка", "coffee"}
_TV_WORDS = {"телевизор", "телевизоры"}
_TABLET_WORDS = {"планшет", "планшеты", "tablet"}
_DESKTOP_WORDS = {"моноблок"}
_ROUTER_WORDS = {"роутер", "роутеры", "маршрутизатор", "маршрутизаторы", "router"}
_REFRIGERATOR_WORDS = {
    "холодильник", "холодильники", "морозильник", "морозильники",
    "морозильная", "холодильно-морозильный",
}
_WASHING_WORDS = {"стиральная", "стиралка"}
_AC_WORDS = {"кондиционер", "кондиционеры"}
_POWER_TOOL_WORDS = {
    "дрель", "перфоратор", "шуруповёрт", "шуруповерт",
    "болгарка", "лобзик", "фрезер", "рубанок", "полировщик", "гравер",
}
_HAND_TOOL_WORDS = {
    "отвёртка", "отвертка", "молоток", "плоскогубцы",
    "ножовка", "зубило", "стамеска", "уровень",
}
_FURNITURE_WORDS = {
    "стул", "стулья", "кресло", "кресла", "стеллаж", "стеллажи",
    "диван", "кровать", "тумба", "комод", "полка", "полки",
}
_UPS_WORDS = {"ибп", "бесперебойник", "ups"}
_LIGHTING_WORDS = {
    "светильник", "светильники", "люстра", "люстры",
    "прожектор", "прожекторы", "бра", "торшер",
}
_LAMP_WORDS = {"лампа", "лампочка", "лампы", "лампочки"}
_CAMERA_WORDS = {
    "фотоаппарат", "фотоаппараты", "фотокамера",
    "зеркальный", "беззеркальный", "видеокамера", "фотограф",
}
_SMALL_APPLIANCE_WORDS = {
    "утюг", "фен", "тостер", "блендер", "мясорубка",
    "миксер", "мультиварка", "пароварка", "кипятильник", "соковыжималка",
}

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
# Standalone-параметры — "ширина 200", "высота 55", "R18" / "диаметр 18".
_TYRE_WIDTH_RE = re.compile(r"\b(?:ширина(?:\s*профиля)?|width)\s*(?P<n>\d{3})\b")
_TYRE_PROFILE_RE = re.compile(r"\b(?:высота(?:\s*профиля)?|profile)\s*(?P<n>\d{2})\b")
_TYRE_RIM_RE = re.compile(r"\b(?:r|радиус|диаметр|посадочный\s*диаметр)\s*(?P<n>1[2-9]|2[0-4])\b")
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
_STAPLE_SIZE_RE = re.compile(r"(?:№|no\.?|n\.?)?\s*(?P<a>\d{1,2})\s*/\s*(?P<b>\d{1,2})\b|(?:№|no\.?|n\.?)\s*(?P<n>\d{1,2})")
_SHEET_CAPACITY_RE = re.compile(r"\b(?:до\s*)?(?P<n>\d{1,3})\s*(?:лист|листов|л\.)\b")
_HEIGHT_RE = re.compile(r"\b(?P<n>\d{2,3})\s*(?:см|cm)\b")
_SCREEN_SIZE_RE = re.compile(r"\b(?P<n>\d{2}(?:[.,]\d)?)\s*(?:\"|дюйм|дюймов|inch|in)\b")
_REFRESH_RE = re.compile(r"\b(?P<n>\d{2,3})\s*(?:гц|hz)\b")
_RESOLUTION_RE = re.compile(r"\b(?P<w>\d{3,4})\s*[xх]\s*(?P<h>\d{3,4})\b")
_BRIGHTNESS_RE = re.compile(r"\b(?P<n>\d{3,5})\s*(?:лм|lm|ansi)\b")
_BIN_VOLUME_RE = re.compile(r"\b(?P<n>\d{1,3})\s*(?:л|l)\b")
_MICRONS_RE = re.compile(r"\b(?P<n>\d{2,4})\s*(?:мкм|mic|micron)\b")
_SECURITY_LEVEL_RE = re.compile(r"\bp[-\s]?(?P<n>[1-7])\b")
_POWER_RE = re.compile(r"\b(?P<n>\d{1,5})\s*(?:вт|w)\b", re.IGNORECASE)
_CAPACITY_L_RE = re.compile(r"\b(?P<n>\d{1,4})\s*(?:л(?:итр(?:ов|а)?)?)\b")
_LOAD_KG_RE = re.compile(r"\b(?P<n>\d{1,2}(?:[.,]\d)?)\s*(?:кг|kg)\b", re.IGNORECASE)
_ENERGY_CLASS_RE = re.compile(r"(?:класс|class)\s*(?P<c>a\+{0,3}|b|c|d|e|f|g)\b|\b(?P<c2>a\+{1,3})\b", re.IGNORECASE)
_SPEED_MBPS_RE = re.compile(r"\b(?P<n>\d{1,5})\s*(?:мбит(?:/с)?|mbps|мб/с)\b|\b(?P<ng>\d{1,5})\s*(?:гбит(?:/с)?|gbps)\b", re.IGNORECASE)
_MEGAPIXELS_RE = re.compile(r"\b(?P<n>\d{1,3})\s*(?:мп|mp|mpx)\b", re.IGNORECASE)
_COLOR_TEMP_RE = re.compile(r"\b(?P<n>[2-9]\d{3})\s*[кk]\b", re.IGNORECASE)
_IPAD_RE = re.compile(r"\bipad\s*(?P<line>pro|air|mini)?\s*(?P<gen>\d)?\b", re.IGNORECASE)


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
    if "колесо в сборе" in cleaned or "колеса в сборе" in cleaned:
        return "wheel_assembly"
    if _TYRE_RE.search(cleaned) or tokens & {"шина", "шины", "резина", "покрышка"}:
        return "tyre"
    if tokens & {"принтер", "мфу", "сканер", "копир", "плоттер"}:
        return "office_equipment"
    if tokens & _OFFICE_MISC_WORDS:
        return "office_equipment"
    if tokens & _OFFICE_SUPPLY_WORDS:
        return "office_supply"
    if tokens & _APPAREL_WORDS:
        return "apparel"
    if tokens & _CARTRIDGE_WORDS:
        return "cartridge"
    if tokens & _PAPER_WORDS:
        return "paper"
    # Large appliances (before monitor — "холодильник" > "монитор")
    if tokens & _REFRIGERATOR_WORDS:
        return "refrigerator"
    if "стиральная машина" in cleaned or "стиральные машины" in cleaned or tokens & _WASHING_WORDS and "машина" in tokens:
        return "washing_machine"
    if tokens & _AC_WORDS:
        return "air_conditioner"
    # AV/display
    if tokens & _TV_WORDS:
        return "tv"
    if tokens & _MONITOR_WORDS:
        return "monitor"
    if tokens & _PROJECTOR_WORDS:
        return "projector"
    # Storage / peripherals
    if tokens & _STORAGE_WORDS:
        return "storage"
    if tokens & _INPUT_DEVICE_WORDS:
        return "input_device"
    # Networking / UPS
    if tokens & _ROUTER_WORDS:
        return "router"
    if tokens & _UPS_WORDS or "источник бесперебойного питания" in cleaned:
        return "ups"
    # Apparel accessories
    if tokens & _GLOVES_WORDS:
        return "apparel"
    # Coffee / small appliances
    if tokens & _COFFEE_WORDS:
        return "coffee_machine"
    if tokens & _SMALL_APPLIANCE_WORDS:
        return "small_appliance"
    if tokens & {"чайник"} and "электр" in cleaned:
        return "small_appliance"
    # Headphones / vacuums
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
    # Power / hand tools
    if tokens & _POWER_TOOL_WORDS:
        return "power_tool"
    if tokens & _HAND_TOOL_WORDS:
        return "hand_tool"
    # Lighting
    if tokens & _LIGHTING_WORDS:
        return "lighting"
    if tokens & _LAMP_WORDS and (tokens & {"светодиодная", "led", "люминесцентная", "накаливания", "энергосберегающая"} or "лампа" in cleaned):
        return "lighting"
    # Camera
    if tokens & _CAMERA_WORDS:
        return "camera"
    # Smartphones / tablets / desktops / laptops
    if tokens & _ACCESSORY_WORDS:
        return "accessory"
    if _IPHONE_RE.search(cleaned) or tokens & {"смартфон", "smartphone", "телефон"}:
        return "smartphone"
    if tokens & _TABLET_WORDS or _IPAD_RE.search(cleaned):
        return "tablet"
    if tokens & {"ноутбук", "laptop", "macbook"}:
        return "laptop"
    if "системный блок" in cleaned or tokens & _DESKTOP_WORDS or "персональный компьютер" in cleaned:
        return "desktop"
    # Furniture (late — many words overlap with generic text)
    if "рабочий стол" in cleaned or "письменный стол" in cleaned or tokens & _FURNITURE_WORDS:
        return "furniture"
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
    if "xbox series x" in cleaned:
        return "xbox series x"
    if "xbox series s" in cleaned:
        return "xbox series s"
    ipad = _IPAD_RE.search(cleaned)
    if ipad:
        parts = ["ipad", ipad.group("line") or "", ipad.group("gen") or ""]
        return " ".join(p for p in parts if p)
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
    # Standalone parameters — "ширина 200", "высота 55", "R18". Срабатывают
    # когда нет полного 205/55R16, например в коротком запросе.
    if "tyre_width_mm" not in out:
        wm = _TYRE_WIDTH_RE.search(cleaned)
        if wm:
            out["tyre_width_mm"] = int(wm.group("n"))
    if "tyre_profile" not in out:
        pm = _TYRE_PROFILE_RE.search(cleaned)
        if pm:
            out["tyre_profile"] = int(pm.group("n"))
    if "tyre_rim_inch" not in out:
        rm = _TYRE_RIM_RE.search(cleaned)
        if rm:
            out["tyre_rim_inch"] = int(rm.group("n"))
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
    if "двусторон" in cleaned:
        out["duplex"] = True
    fmt = _PAPER_FORMAT_RE.search(cleaned)
    if fmt:
        out["paper_format"] = f"A{fmt.group('n')}"
    capacity = _SHEET_CAPACITY_RE.search(cleaned)
    if capacity and out.get("device_type") in {"shredder", "hole_punch"}:
        out["sheet_capacity"] = int(capacity.group("n"))
    security = _SECURITY_LEVEL_RE.search(cleaned)
    if security:
        out["security_level"] = f"P-{security.group('n')}"
    bin_volume = _BIN_VOLUME_RE.search(cleaned)
    if bin_volume and out.get("device_type") == "shredder":
        out["bin_volume_l"] = int(bin_volume.group("n"))
    thickness = _MICRONS_RE.search(cleaned)
    if thickness and out.get("device_type") == "laminator":
        out["laminating_thickness_microns"] = int(thickness.group("n"))
    return out


def _extract_office_supply(cleaned: str, tokens: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "степлер" in tokens:
        out["device_type"] = "stapler"
    elif "дырокол" in tokens:
        out["device_type"] = "hole_punch"
    elif tokens & {"скобы", "скоба"}:
        out["device_type"] = "staples"
    elif tokens & {"скрепки", "скрепка"}:
        out["device_type"] = "paper_clips"
    elif tokens & {"папка", "папки", "файл", "файлы"}:
        out["device_type"] = "folder"
    elif tokens & {"ручка", "ручки"}:
        out["device_type"] = "pen"
    elif tokens & {"маркер", "маркеры"}:
        out["device_type"] = "marker"
    elif tokens & {"карандаш", "карандаши"}:
        out["device_type"] = "pencil"

    staple = _STAPLE_SIZE_RE.search(cleaned)
    if staple:
        out["staple_size"] = f"№{int(staple.group('n'))}" if staple.group("n") else f"{int(staple.group('a'))}/{int(staple.group('b'))}"
    capacity = _SHEET_CAPACITY_RE.search(cleaned)
    if capacity:
        out["sheet_capacity"] = int(capacity.group("n"))
    return out


def _extract_apparel(cleaned: str, tokens: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    apparel_type = _first_token_match(tokens, _APPAREL_TYPE_MAP)
    if apparel_type:
        out["apparel_type"] = apparel_type
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
    height = _HEIGHT_RE.search(cleaned)
    if height:
        n = int(height.group("n"))
        if 50 <= n <= 230:
            out["height_cm"] = n
    if "зим" in cleaned or "winter" in cleaned:
        out["season"] = "winter"
    elif "летн" in cleaned or "summer" in cleaned:
        out["season"] = "summer"
    if "пух" in cleaned:
        out["insulation"] = "down"
    elif "синтепон" in cleaned:
        out["insulation"] = "synthetic"
    return out


def _extract_monitor(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    screen = _SCREEN_SIZE_RE.search(cleaned)
    if screen:
        out["screen_size_inch"] = float(screen.group("n").replace(",", "."))
    refresh = _REFRESH_RE.search(cleaned)
    if refresh:
        out["refresh_rate_hz"] = int(refresh.group("n"))
    resolution = _RESOLUTION_RE.search(cleaned)
    if resolution:
        out["resolution"] = f"{resolution.group('w')}x{resolution.group('h')}"
    for matrix in ("ips", "va", "tn", "oled"):
        if matrix in cleaned:
            out["matrix_type"] = matrix.upper()
            break
    return out


def _extract_projector(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {"device_type": "projector"}
    resolution = _RESOLUTION_RE.search(cleaned)
    if resolution:
        out["resolution"] = f"{resolution.group('w')}x{resolution.group('h')}"
    elif "full hd" in cleaned or "fhd" in cleaned:
        out["resolution"] = "1920x1080"
    elif "4k" in cleaned:
        out["resolution"] = "3840x2160"
    brightness = _BRIGHTNESS_RE.search(cleaned)
    if brightness:
        out["brightness_lm"] = int(brightness.group("n"))
    return out


def _extract_storage(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "nvme" in cleaned:
        out["interface"] = "nvme"
    elif "sata" in cleaned:
        out["interface"] = "sata"
    elif "usb" in cleaned:
        out["interface"] = "usb"
    return out


def _extract_input_device(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "беспровод" in cleaned or "wireless" in cleaned or "bluetooth" in cleaned:
        out["connection_type"] = "wireless"
    elif "провод" in cleaned or "wired" in cleaned or "usb" in cleaned:
        out["connection_type"] = "wired"
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
    match = re.search(r"\b(?:hp|canon|xerox|brother|kyocera|pantum|ricoh)\s+[\w-]+\b", cleaned)
    if match:
        out["model"] = match.group(0)
    return out


def _extract_tv(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    screen = _SCREEN_SIZE_RE.search(cleaned)
    if screen:
        out["screen_size_inch"] = float(screen.group("n").replace(",", "."))
    refresh = _REFRESH_RE.search(cleaned)
    if refresh:
        out["refresh_rate_hz"] = int(refresh.group("n"))
    resolution = _RESOLUTION_RE.search(cleaned)
    if resolution:
        out["resolution"] = f"{resolution.group('w')}x{resolution.group('h')}"
    elif "4k" in cleaned or "ultra hd" in cleaned or "uhd" in cleaned:
        out["resolution"] = "3840x2160"
    elif "full hd" in cleaned or "fhd" in cleaned:
        out["resolution"] = "1920x1080"
    elif " hd" in cleaned or cleaned.startswith("hd "):
        out["resolution"] = "1280x720"
    if "smart" in cleaned or "смарт" in cleaned:
        out["wifi"] = True
    return out


def _extract_tablet(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    screen = _SCREEN_SIZE_RE.search(cleaned)
    if screen:
        out["screen_size_inch"] = float(screen.group("n").replace(",", "."))
    split = _SPLIT_MEM_RE.search(cleaned)
    if split:
        out["ram_gb"] = int(split.group("ram"))
        out["storage_gb"] = int(split.group("storage"))
    else:
        out["storage_gb"] = _extract_storage_gb(cleaned)
    if "wifi" in cleaned or "wi-fi" in cleaned:
        out["wifi"] = True
    return out


def _extract_desktop(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ram, storage = _infer_laptop_memory(cleaned)
    if ram:
        out["ram_gb"] = ram
    if storage:
        out["storage_gb"] = storage
    return out


def _extract_router(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {"wifi": True}
    speed = _SPEED_MBPS_RE.search(cleaned)
    if speed:
        if speed.group("ng"):
            out["speed_mbps"] = int(speed.group("ng")) * 1000
        elif speed.group("n"):
            out["speed_mbps"] = int(speed.group("n"))
    return out


def _extract_refrigerator(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cap = _CAPACITY_L_RE.search(cleaned)
    if cap:
        n = int(cap.group("n"))
        if 50 <= n <= 2000:
            out["capacity_l"] = n
    energy = _ENERGY_CLASS_RE.search(cleaned)
    if energy:
        cls = (energy.group("c") or energy.group("c2") or "").upper()
        if cls:
            out["energy_class"] = cls
    return out


def _extract_washing(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    load = _LOAD_KG_RE.search(cleaned)
    if load:
        n = float(load.group("n").replace(",", "."))
        if 1 <= n <= 30:
            out["load_kg"] = n
    energy = _ENERGY_CLASS_RE.search(cleaned)
    if energy:
        cls = (energy.group("c") or energy.group("c2") or "").upper()
        if cls:
            out["energy_class"] = cls
    return out


def _extract_power_tool(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    power = _POWER_RE.search(cleaned)
    if power:
        n = int(power.group("n"))
        if 10 <= n <= 10000:
            out["power_w"] = n
    return out


def _extract_lighting(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    power = _POWER_RE.search(cleaned)
    if power:
        n = int(power.group("n"))
        if 1 <= n <= 5000:
            out["power_w"] = n
    brightness = _BRIGHTNESS_RE.search(cleaned)
    if brightness:
        out["brightness_lm"] = int(brightness.group("n"))
    color_temp = _COLOR_TEMP_RE.search(cleaned)
    if color_temp:
        out["color_temperature_k"] = int(color_temp.group("n"))
    return out


def _extract_camera(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    mp = _MEGAPIXELS_RE.search(cleaned)
    if mp:
        n = int(mp.group("n"))
        if 1 <= n <= 500:
            out["megapixels"] = n
    return out


def _extract_small_appliance(cleaned: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    power = _POWER_RE.search(cleaned)
    if power:
        n = int(power.group("n"))
        if 10 <= n <= 10000:
            out["power_w"] = n
    cap = _CAPACITY_L_RE.search(cleaned)
    if cap:
        n = int(cap.group("n"))
        if 1 <= n <= 100:
            out["capacity_l"] = n
    return out


def _confidence(values: dict[str, Any]) -> float:
    keys = [
        "category", "brand", "model", "color", "storage_gb",
        "tyre_width_mm", "tyre_profile", "tyre_rim_inch",
        "season", "device_type", "print_technology", "apparel_type", "size", "gender", "material",
        "paper_format", "density_gm2", "sheets_count", "staple_size", "sheet_capacity",
        "page_yield", "pack_count", "duplex", "print_speed_ppm", "screen_size_inch",
        "refresh_rate_hz", "resolution", "matrix_type", "brightness_lm", "interface",
        "connection_type", "security_level", "bin_volume_l", "laminating_thickness_microns",
        "capacity_l", "power_w", "energy_class", "load_kg",
        "speed_mbps", "megapixels", "color_temperature_k",
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
    text_cleaned = _clean(text)
    text_tokens = _tokens(text_cleaned)
    cleaned = _clean(blob)
    tokens = _tokens(cleaned)

    structured: dict[str, Any] = {}
    extra_raw: dict[str, str] = {}
    has_structured = False
    if characteristics:
        structured, extra_raw = normalize_structured_characteristics(characteristics)
        has_structured = bool(structured)

    values: dict[str, Any] = {
        "category": _detect_category(text_cleaned, text_tokens) or _detect_category(cleaned, tokens),
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
        "wifi", "color_print", "print_technology", "device_type", "duplex", "print_speed_ppm",
        "density_gm2", "sheets_count", "paper_format", "pack_count",
        "staple_size", "sheet_capacity", "binding_depth_mm",
        "page_yield", "original_consumable", "compatible_models",
        "apparel_type", "height_cm", "insulation",
        "screen_size_inch", "refresh_rate_hz", "resolution", "matrix_type",
        "brightness_lm", "interface", "connection_type", "security_level",
        "bin_volume_l", "laminating_thickness_microns",
        "tyre_width_mm", "tyre_profile", "tyre_rim_inch",
        "gender", "material", "size",
        "capacity_l", "power_w", "energy_class", "load_kg",
        "speed_mbps", "megapixels", "color_temperature_k",
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
        for field in (
            "device_type", "print_technology", "color_print", "wifi", "duplex",
            "print_speed_ppm", "paper_format", "sheet_capacity", "security_level",
            "bin_volume_l", "laminating_thickness_microns",
        ):
            if field in structured:
                values[field] = structured[field]
    if category == "office_supply":
        supply = _extract_office_supply(cleaned, tokens)
        for k, v in supply.items():
            values.setdefault(k, v)
        for field in ("device_type", "staple_size", "sheet_capacity", "binding_depth_mm", "material", "color", "pack_count"):
            if field in structured:
                values[field] = structured[field]
    if category == "apparel":
        apparel = _extract_apparel(cleaned, tokens)
        for k, v in apparel.items():
            values.setdefault(k, v)
        for field in ("apparel_type", "size", "gender", "material", "season", "height_cm", "insulation"):
            if field in structured:
                values[field] = structured[field]
    if category == "monitor":
        monitor = _extract_monitor(cleaned)
        for k, v in monitor.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "screen_size_inch", "refresh_rate_hz", "resolution", "matrix_type"):
            if field in structured:
                values[field] = structured[field]
    if category == "projector":
        projector = _extract_projector(cleaned)
        for k, v in projector.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "resolution", "brightness_lm", "wifi"):
            if field in structured:
                values[field] = structured[field]
    if category == "storage":
        storage = _extract_storage(cleaned)
        for k, v in storage.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "storage_gb", "interface"):
            if field in structured:
                values[field] = structured[field]
    if category == "input_device":
        input_device = _extract_input_device(cleaned)
        for k, v in input_device.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "connection_type", "interface"):
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
        for field in ("brand", "model", "color", "page_yield", "pack_count", "original_consumable", "compatible_models"):
            if field in structured:
                values[field] = structured[field]
    if category == "tv":
        tv = _extract_tv(cleaned)
        for k, v in tv.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "screen_size_inch", "refresh_rate_hz", "resolution", "wifi"):
            if field in structured:
                values[field] = structured[field]
    if category == "tablet":
        tablet = _extract_tablet(cleaned)
        for k, v in tablet.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "screen_size_inch", "storage_gb", "ram_gb", "color", "wifi"):
            if field in structured:
                values[field] = structured[field]
    if category == "desktop":
        desktop = _extract_desktop(cleaned)
        for k, v in desktop.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "ram_gb", "storage_gb"):
            if field in structured:
                values[field] = structured[field]
    if category == "router":
        router = _extract_router(cleaned)
        for k, v in router.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "speed_mbps", "wifi"):
            if field in structured:
                values[field] = structured[field]
    if category == "refrigerator":
        fridge = _extract_refrigerator(cleaned)
        for k, v in fridge.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "capacity_l", "energy_class", "color"):
            if field in structured:
                values[field] = structured[field]
    if category == "washing_machine":
        washing = _extract_washing(cleaned)
        for k, v in washing.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "load_kg", "energy_class"):
            if field in structured:
                values[field] = structured[field]
    if category == "air_conditioner":
        power = _POWER_RE.search(cleaned)
        if power:
            values.setdefault("power_w", int(power.group("n")))
        for field in ("brand", "model", "power_w"):
            if field in structured:
                values[field] = structured[field]
    if category in {"power_tool", "hand_tool"}:
        tool = _extract_power_tool(cleaned)
        for k, v in tool.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "power_w"):
            if field in structured:
                values[field] = structured[field]
    if category == "lighting":
        lighting = _extract_lighting(cleaned)
        for k, v in lighting.items():
            values.setdefault(k, v)
        for field in ("brand", "power_w", "brightness_lm", "color_temperature_k"):
            if field in structured:
                values[field] = structured[field]
    if category == "camera":
        camera = _extract_camera(cleaned)
        for k, v in camera.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "megapixels"):
            if field in structured:
                values[field] = structured[field]
    if category in {"small_appliance", "coffee_machine", "vacuum_cleaner"}:
        sa = _extract_small_appliance(cleaned)
        for k, v in sa.items():
            values.setdefault(k, v)
        for field in ("brand", "model", "power_w", "capacity_l"):
            if field in structured:
                values[field] = structured[field]
    if category == "ups":
        power = _POWER_RE.search(cleaned)
        if power:
            values.setdefault("power_w", int(power.group("n")))
        for field in ("brand", "model", "power_w"):
            if field in structured:
                values[field] = structured[field]

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
        return [
            "category", "brand", "model", "device_type", "print_technology",
            "color_print", "wifi", "duplex", "paper_format", "print_speed_ppm",
            "sheet_capacity", "security_level", "bin_volume_l", "laminating_thickness_microns",
        ]
    if category == "office_supply":
        return [
            "category", "brand", "device_type", "staple_size",
            "sheet_capacity", "binding_depth_mm", "color", "material", "pack_count",
        ]
    if category == "apparel":
        return ["category", "brand", "apparel_type", "color", "size", "gender", "material", "season", "height_cm", "insulation"]
    if category == "paper":
        return ["category", "brand", "paper_format", "density_gm2", "sheets_count", "pack_count", "color"]
    if category == "cartridge":
        return [
            "category", "brand", "model", "color", "page_yield",
            "original_consumable", "pack_count", "compatible_models",
        ]
    if category in {"headphones", "laptop", "robot_vacuum"}:
        return ["category", "brand", "model", "color", "storage_gb", "ram_gb"]
    if category == "monitor":
        return ["category", "brand", "model", "screen_size_inch", "refresh_rate_hz", "resolution", "matrix_type"]
    if category == "tv":
        return ["category", "brand", "model", "screen_size_inch", "resolution", "refresh_rate_hz", "wifi"]
    if category == "tablet":
        return ["category", "brand", "model", "screen_size_inch", "storage_gb", "ram_gb", "color"]
    if category == "desktop":
        return ["category", "brand", "model", "ram_gb", "storage_gb"]
    if category == "projector":
        return ["category", "brand", "model", "resolution", "brightness_lm", "wifi"]
    if category == "storage":
        return ["category", "brand", "model", "storage_gb", "interface"]
    if category == "input_device":
        return ["category", "brand", "model", "connection_type", "interface"]
    if category == "router":
        return ["category", "brand", "model", "speed_mbps", "wifi"]
    if category == "refrigerator":
        return ["category", "brand", "model", "capacity_l", "energy_class", "color"]
    if category == "washing_machine":
        return ["category", "brand", "model", "load_kg", "energy_class"]
    if category == "air_conditioner":
        return ["category", "brand", "model", "power_w"]
    if category in {"power_tool", "hand_tool"}:
        return ["category", "brand", "model", "power_w"]
    if category == "lighting":
        return ["category", "brand", "power_w", "brightness_lm", "color_temperature_k", "color"]
    if category == "camera":
        return ["category", "brand", "model", "megapixels"]
    if category == "ups":
        return ["category", "brand", "model", "power_w"]
    if category in {"small_appliance", "coffee_machine", "vacuum_cleaner"}:
        return ["category", "brand", "model", "power_w", "capacity_l"]
    if category == "furniture":
        return ["category", "brand", "model", "material", "color"]
    return common


def _weight(field: str) -> float:
    return {
        "category": 2.0, "brand": 2.0, "model": 3.0, "color": 1.0,
        "storage_gb": 1.5, "ram_gb": 1.0,
        "tyre_width_mm": 2.0, "tyre_profile": 2.0, "tyre_rim_inch": 2.0,
        "season": 1.5, "studs": 1.0,
        "device_type": 2.0, "print_technology": 1.5, "color_print": 1.0, "wifi": 0.7,
        "duplex": 0.7, "print_speed_ppm": 0.5,
        "apparel_type": 2.0, "size": 1.8, "gender": 0.8, "material": 0.8,
        "height_cm": 0.6, "insulation": 0.7,
        "paper_format": 1.8, "density_gm2": 1.4, "sheets_count": 1.4,
        "pack_count": 0.8,
        "staple_size": 2.0, "sheet_capacity": 1.3, "binding_depth_mm": 0.6,
        "page_yield": 1.3, "original_consumable": 1.0, "compatible_models": 1.2,
        "screen_size_inch": 1.5, "refresh_rate_hz": 1.2, "resolution": 1.5,
        "matrix_type": 0.8, "brightness_lm": 1.1, "interface": 0.8,
        "connection_type": 0.8, "security_level": 1.2, "bin_volume_l": 0.6,
        "laminating_thickness_microns": 0.8,
        "capacity_l": 1.5, "power_w": 1.2, "energy_class": 1.2, "load_kg": 1.8,
        "speed_mbps": 1.5, "megapixels": 1.2, "color_temperature_k": 1.0,
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
_SOFT_MISMATCH_FIELDS = {
    "color", "storage_gb", "ram_gb", "season", "size",
    "density_gm2", "sheets_count", "pack_count", "sheet_capacity",
    "page_yield", "print_speed_ppm", "screen_size_inch", "refresh_rate_hz",
    "brightness_lm", "bin_volume_l", "laminating_thickness_microns", "height_cm",
}
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
