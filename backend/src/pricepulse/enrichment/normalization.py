"""Deterministic normalizers and key-alias tables for marketplace characteristics.

Maps Russian/English marketplace spec keys → canonical ProductAttributes fields
and normalises raw string values to typed Python values.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from rapidfuzz import fuzz, process

from pricepulse.domain.models import CanonicalAttribute, CanonicalProduct, ScalarAttribute

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
    "ширина профиля, мм": "tyre_width_mm",
    "ширина": "tyre_width_mm",
    "ширина шины": "tyre_width_mm",
    "высота профиля": "tyre_profile",
    "высота профиля, %": "tyre_profile",
    "высота": "tyre_profile",
    "посадочный диаметр": "tyre_rim_inch",
    "диаметр диска": "tyre_rim_inch",
    "диаметр, дюймы": "tyre_rim_inch",
    "диаметр": "tyre_rim_inch",
    "радиус": "tyre_rim_inch",
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
    "беспроводные интерфейсы": "wifi",
    "взаимодействие с устройствами": "wifi",
    "цветность печати": "color_print",
    "максимальный формат печати": "paper_format",
    "максимальный формат": "paper_format",
    "автоматическая двусторонняя печать": "duplex",
    "двусторонняя печать": "duplex",
    "скорость печати а4": "print_speed_ppm",
    "макс. скорость печати (ч/б), стр/мин": "print_speed_ppm",
    "скорость черно-белой печати": "print_speed_ppm",
    "скорость цветной печати": "print_speed_ppm",
    "диагональ": "screen_size_inch",
    "диагональ экрана": "screen_size_inch",
    "частота обновления": "refresh_rate_hz",
    "частота обновления экрана": "refresh_rate_hz",
    "разрешение": "resolution",
    "разрешение экрана": "resolution",
    "максимальное разрешение": "resolution",
    "тип матрицы": "matrix_type",
    "матрица": "matrix_type",
    "яркость": "brightness_lm",
    "световой поток": "brightness_lm",
    "интерфейс": "interface",
    "интерфейсы": "interface",
    "интерфейс подключения": "interface",
    "тип подключения": "connection_type",
    "подключение": "connection_type",
    "уровень секретности": "security_level",
    "класс секретности": "security_level",
    "степень секретности": "security_level",
    "объем корзины": "bin_volume_l",
    "объем контейнера": "bin_volume_l",
    "объем корзины, л": "bin_volume_l",
    "толщина пленки": "laminating_thickness_microns",
    "максимальная толщина пленки": "laminating_thickness_microns",
    "толщина ламинирования": "laminating_thickness_microns",
    # Office supplies / consumables.
    "количество листов скрепления": "sheet_capacity",
    "пробивная способность": "sheet_capacity",
    "пробивная способность, листов": "sheet_capacity",
    "размер скоб": "staple_size",
    "размер скоб для канцелярского степлера": "staple_size",
    "максимальная глубина закладки бумаги": "binding_depth_mm",
    "ресурс": "page_yield",
    "максимальный ресурс": "page_yield",
    "ресурс картриджа": "page_yield",
    "цвет тонера/чернил": "color",
    "цвет картриджа/чернил": "color",
    "количество в упаковке, шт": "pack_count",
    "количество предметов в упаковке": "pack_count",
    "оригинальность расходника": "original_consumable",
    "совместимость картриджа": "compatible_models",
    "код производителя картриджей для принтеров": "model",
    # Large / home appliances
    "объем камеры": "capacity_l",
    "полный объем холодильника": "capacity_l",
    "общий объем": "capacity_l",
    "объем": "capacity_l",
    "мощность": "power_w",
    "потребляемая мощность": "power_w",
    "номинальная мощность": "power_w",
    "класс энергоэффективности": "energy_class",
    "класс энергопотребления": "energy_class",
    "класс энергии": "energy_class",
    "максимальная загрузка": "load_kg",
    "загрузка белья": "load_kg",
    "максимальная нагрузка": "load_kg",
    # Networking
    "скорость передачи данных": "speed_mbps",
    "максимальная скорость": "speed_mbps",
    "скорость wan": "speed_mbps",
    "пропускная способность": "speed_mbps",
    # Cameras
    "количество мегапикселей": "megapixels",
    "разрешение матрицы": "megapixels",
    "разрешение основной камеры": "megapixels",
    "количество пикселей": "megapixels",
    # Lighting
    "цветовая температура": "color_temperature_k",
    "цветовой тон": "color_temperature_k",
    "цветовая температура света": "color_temperature_k",
    # Apparel
    "размер": "size",
    "российский размер": "size",
    "размер одежды": "size",
    "международный размер": "size",
    "размер производителя": "size",
    "пол": "gender",
    "половозрастная группа": "gender",
    "для кого": "gender",
    "пол/возраст": "gender",
    "вид одежды": "apparel_type",
    "тип одежды": "apparel_type",
    "предмет одежды": "apparel_type",
    "рост": "height_cm",
    "рост, см": "height_cm",
    "утеплитель": "insulation",
    "материал": "material",
    "состав": "material",
    "ткань": "material",
    "материал верха": "material",
}

_FIELD_UNITS: dict[str, str] = {
    "storage_gb": "GB",
    "ram_gb": "GB",
    "density_gm2": "g/m2",
    "sheets_count": "sheets",
    "tyre_width_mm": "mm",
    "tyre_profile": "%",
    "tyre_rim_inch": "inch",
    "binding_depth_mm": "mm",
    "height_cm": "cm",
    "screen_size_inch": "inch",
    "refresh_rate_hz": "Hz",
    "brightness_lm": "lm",
    "bin_volume_l": "l",
    "laminating_thickness_microns": "micron",
    "print_speed_ppm": "ppm",
    "sheet_capacity": "sheets",
    "page_yield": "pages",
    "pack_count": "pcs",
    "capacity_l": "l",
    "power_w": "W",
    "load_kg": "kg",
    "speed_mbps": "Mbps",
    "megapixels": "MP",
    "color_temperature_k": "K",
}

_STRING_FIELDS = {
    "brand", "model", "size", "gender", "material", "paper_format",
    "staple_size", "compatible_models", "apparel_type", "insulation",
    "resolution", "matrix_type", "interface", "connection_type", "security_level",
    "energy_class",
}
_KEY_MATCH_CUTOFF = 88

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


def normalize_wifi_str(value: str) -> bool | None:
    normed = _nrm(value)
    if any(w in normed for w in ("wi-fi", "wifi", "wi fi", "беспровод")):
        return True
    return normalize_boolean_str(value)


def normalize_color_print_str(value: str) -> bool | None:
    normed = _nrm(value)
    if any(w in normed for w in ("цветн", "color")):
        return True
    if any(w in normed for w in ("монохром", "черно-бел", "ч/б", "black and white")):
        return False
    return normalize_boolean_str(value)


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


_APPAREL_TYPE_MAP = {
    "футболк": "t_shirt",
    "майк": "t_shirt",
    "худи": "hoodie",
    "толстовк": "hoodie",
    "куртк": "jacket",
    "пуховик": "down_jacket",
    "брюк": "pants",
    "штан": "pants",
    "джинс": "jeans",
    "рубашк": "shirt",
    "плать": "dress",
    "нос": "socks",
    "костюм": "suit",
    "юбк": "skirt",
}


def normalize_apparel_type_str(value: str) -> str | None:
    normed = _nrm(value)
    for key, canonical in _APPAREL_TYPE_MAP.items():
        if key in normed:
            return canonical
    return None


def normalize_height_cm_str(value: str) -> int | None:
    match = re.search(r"(\d{2,3})", value)
    if not match:
        return None
    n = int(match.group(1))
    return n if 50 <= n <= 230 else None


def normalize_insulation_str(value: str) -> str | None:
    normed = _nrm(value)
    if "пух" in normed:
        return "down"
    if "синтепон" in normed or "полиэстер" in normed:
        return "synthetic"
    if "без утеп" in normed:
        return "none"
    return normed or None


_MATERIAL_MAP = {
    "хлопок": "cotton",
    "cotton": "cotton",
    "полиэстер": "polyester",
    "polyester": "polyester",
    "шерсть": "wool",
    "wool": "wool",
    "кожа": "leather",
    "leather": "leather",
    "флис": "fleece",
    "нейлон": "nylon",
    "nylon": "nylon",
}


def normalize_material_str(value: str) -> str | None:
    normed = _nrm(value)
    for key, canonical in _MATERIAL_MAP.items():
        if key in normed:
            return canonical
    return normed or None


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
_STAPLE_SIZE_RE = re.compile(r"(?:№|no\.?|n\.?)?\s*(\d{1,2})\s*/\s*(\d{1,2})|(?:№|no\.?|n\.?)\s*(\d{1,2})", re.IGNORECASE)


def normalize_sheets_str(value: str) -> int | None:
    match = _SHEETS_RE.search(value)
    if match:
        n = int(match.group(1))
        if 10 <= n <= 10000:
            return n
    return None


def normalize_count_str(value: str) -> int | None:
    match = re.search(r"(\d{1,6})", value.replace(" ", ""))
    if not match:
        return None
    n = int(match.group(1))
    return n if n > 0 else None


def normalize_print_speed_str(value: str) -> int | None:
    match = re.search(r"(\d{1,3})", value)
    if not match:
        return None
    n = int(match.group(1))
    return n if 1 <= n <= 300 else None


def normalize_screen_size_str(value: str) -> float | None:
    match = re.search(r"(\d{2}(?:[.,]\d)?)", value)
    if not match:
        return None
    n = float(match.group(1).replace(",", "."))
    return n if 5 <= n <= 120 else None


def normalize_refresh_rate_str(value: str) -> int | None:
    match = re.search(r"(\d{2,3})", value)
    if not match:
        return None
    n = int(match.group(1))
    return n if 30 <= n <= 500 else None


def normalize_resolution_str(value: str) -> str | None:
    normed = _nrm(value).replace("×", "x").replace("х", "x")
    match = re.search(r"(\d{3,4})\s*x\s*(\d{3,4})", normed)
    if match:
        return f"{match.group(1)}x{match.group(2)}"
    if "full hd" in normed or "fhd" in normed:
        return "1920x1080"
    if "4k" in normed or "ultra hd" in normed or "uhd" in normed:
        return "3840x2160"
    return None


def normalize_matrix_type_str(value: str) -> str | None:
    normed = _nrm(value)
    for matrix in ("ips", "va", "tn", "oled"):
        if matrix in normed:
            return matrix.upper()
    return None


def normalize_brightness_str(value: str) -> int | None:
    match = re.search(r"(\d{3,5})", value.replace(" ", ""))
    if not match:
        return None
    n = int(match.group(1))
    return n if 100 <= n <= 100000 else None


def normalize_interface_str(value: str) -> str | None:
    normed = _nrm(value)
    if "nvme" in normed:
        return "nvme"
    if "sata" in normed:
        return "sata"
    if "usb" in normed:
        return "usb"
    if "hdmi" in normed:
        return "hdmi"
    if "displayport" in normed or "dp" in normed:
        return "displayport"
    return normed or None


def normalize_connection_type_str(value: str) -> str | None:
    normed = _nrm(value)
    if any(w in normed for w in ("беспровод", "wireless", "bluetooth", "радио")):
        return "wireless"
    if any(w in normed for w in ("провод", "wired", "usb")):
        return "wired"
    return None


def normalize_security_level_str(value: str) -> str | None:
    normed = _nrm(value).upper()
    match = re.search(r"P[-\s]?([1-7])", normed)
    return f"P-{match.group(1)}" if match else None


def normalize_staple_size_str(value: str) -> str | None:
    value = unicodedata.normalize("NFKC", value).lower()
    match = _STAPLE_SIZE_RE.search(value)
    if not match:
        return None
    if match.group(3):
        return f"№{int(match.group(3))}"
    return f"{int(match.group(1))}/{int(match.group(2))}"


def normalize_original_consumable_str(value: str) -> bool | None:
    normed = _nrm(value)
    if any(word in normed for word in ("оригин", "original", "oem")):
        return True
    if any(word in normed for word in ("совмест", "аналог", "compatible")):
        return False
    return normalize_boolean_str(value)


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


def normalize_capacity_l_str(value: str) -> int | None:
    match = re.search(r"(\d+)", value.replace(" ", ""))
    if match:
        n = int(match.group(1))
        return n if 1 <= n <= 5000 else None
    return None


def normalize_power_w_str(value: str) -> int | None:
    match = re.search(r"(\d+)", value.replace(" ", ""))
    if match:
        n = int(match.group(1))
        return n if 1 <= n <= 100000 else None
    return None


def normalize_energy_class_str(value: str) -> str | None:
    normed = _nrm(value).upper()
    match = re.search(r"(A\+{0,3}|B|C|D|E|F|G)", normed)
    return match.group(1) if match else None


def normalize_load_kg_str(value: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)", value)
    if match:
        n = float(match.group(1).replace(",", "."))
        return n if 1 <= n <= 100 else None
    return None


def normalize_speed_mbps_str(value: str) -> int | None:
    normed = _nrm(value)
    match = re.search(r"(\d+)", value.replace(" ", ""))
    if not match:
        return None
    n = int(match.group(1))
    if any(w in normed for w in ("гбит", "gbps", "gb/s")):
        return n * 1000
    return n if 1 <= n <= 100000 else None


def normalize_megapixels_str(value: str) -> int | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)", value)
    if match:
        n = int(round(float(match.group(1).replace(",", "."))))
        return n if 1 <= n <= 500 else None
    return None


def normalize_color_temperature_k_str(value: str) -> int | None:
    match = re.search(r"(\d{4})", value)
    if match:
        n = int(match.group(1))
        return n if 1000 <= n <= 10000 else None
    return None


# Dispatch table: canonical field name → normalizer function
_FIELD_NORMALIZERS: dict[str, Any] = {
    "storage_gb": normalize_memory_str_to_gb,
    "ram_gb": normalize_memory_str_to_gb,
    "color": normalize_color_str,
    "color_print": normalize_color_print_str,
    "wifi": normalize_wifi_str,
    "duplex": normalize_boolean_str,
    "print_speed_ppm": normalize_print_speed_str,
    "screen_size_inch": normalize_screen_size_str,
    "refresh_rate_hz": normalize_refresh_rate_str,
    "resolution": normalize_resolution_str,
    "matrix_type": normalize_matrix_type_str,
    "brightness_lm": normalize_brightness_str,
    "interface": normalize_interface_str,
    "connection_type": normalize_connection_type_str,
    "security_level": normalize_security_level_str,
    "bin_volume_l": normalize_count_str,
    "laminating_thickness_microns": normalize_count_str,
    "studs": normalize_boolean_str,
    "season": normalize_season_str,
    "device_type": normalize_device_type_str,
    "print_technology": normalize_print_tech_str,
    "gender": normalize_gender_str,
    "material": normalize_material_str,
    "size": normalize_size_str,
    "apparel_type": normalize_apparel_type_str,
    "height_cm": normalize_height_cm_str,
    "insulation": normalize_insulation_str,
    "paper_format": normalize_paper_format_str,
    "density_gm2": normalize_density_str,
    "sheets_count": normalize_sheets_str,
    "pack_count": normalize_count_str,
    "staple_size": normalize_staple_size_str,
    "sheet_capacity": normalize_count_str,
    "binding_depth_mm": normalize_count_str,
    "page_yield": normalize_count_str,
    "original_consumable": normalize_original_consumable_str,
    "tyre_width_mm": normalize_tyre_width_str,
    "tyre_profile": normalize_tyre_profile_str,
    "tyre_rim_inch": normalize_tyre_rim_str,
    "capacity_l": normalize_capacity_l_str,
    "power_w": normalize_power_w_str,
    "energy_class": normalize_energy_class_str,
    "load_kg": normalize_load_kg_str,
    "speed_mbps": normalize_speed_mbps_str,
    "megapixels": normalize_megapixels_str,
    "color_temperature_k": normalize_color_temperature_k_str,
}

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _key_contains_any(key: str, markers: tuple[str, ...]) -> bool:
    return any(marker in key for marker in markers)


def _heuristic_key_field(key_normed: str) -> str | None:
    if _key_contains_any(key_normed, ("оператив", "озу", "ram")):
        return "ram_gb"
    if _key_contains_any(key_normed, ("встроенн", "внутренн", "накоп", "rom", "storage", "ssd", "жестк")):
        return "storage_gb"
    if _key_contains_any(key_normed, ("памят",)) and not _key_contains_any(key_normed, ("карта", "слот")):
        return "storage_gb"
    if _key_contains_any(key_normed, ("бренд", "производитель", "торговая марка")):
        return "brand"
    if _key_contains_any(key_normed, ("модель", "mpn")):
        return "model"
    if _key_contains_any(key_normed, ("цвет", "color", "colour")):
        return "color"
    if _key_contains_any(key_normed, ("вид одежды", "тип одежды", "предмет одежды")):
        return "apparel_type"
    if _key_contains_any(key_normed, ("рост",)):
        return "height_cm"
    if _key_contains_any(key_normed, ("утеплител",)):
        return "insulation"
    if _key_contains_any(key_normed, ("формат",)) and _key_contains_any(key_normed, ("бумаг", "лист")):
        return "paper_format"
    if _key_contains_any(key_normed, ("плотност", "грамматур")):
        return "density_gm2"
    if _key_contains_any(key_normed, ("лист",)) and _key_contains_any(key_normed, ("колич", "пачк", "упаков")):
        return "sheets_count"
    if _key_contains_any(key_normed, ("сезон",)):
        return "season"
    if _key_contains_any(key_normed, ("шип",)):
        return "studs"
    if _key_contains_any(key_normed, ("wi-fi", "wifi", "wi fi", "беспровод")):
        return "wifi"
    if _key_contains_any(key_normed, ("диагонал",)):
        return "screen_size_inch"
    if _key_contains_any(key_normed, ("частота", "герц", "гц")) and _key_contains_any(key_normed, ("обнов", "экран")):
        return "refresh_rate_hz"
    if _key_contains_any(key_normed, ("разрешен",)):
        return "resolution"
    if _key_contains_any(key_normed, ("матриц",)):
        return "matrix_type"
    if _key_contains_any(key_normed, ("яркост", "световой поток")):
        return "brightness_lm"
    if _key_contains_any(key_normed, ("интерфейс",)):
        return "interface"
    if _key_contains_any(key_normed, ("подключен", "соединен")):
        return "connection_type"
    if _key_contains_any(key_normed, ("скоб", "степлер")):
        return "staple_size"
    if _key_contains_any(key_normed, ("пробив", "скреплен")):
        return "sheet_capacity"
    if _key_contains_any(key_normed, ("глубин", "закладк")):
        return "binding_depth_mm"
    if _key_contains_any(key_normed, ("ресурс",)):
        return "page_yield"
    if _key_contains_any(key_normed, ("колич",)) and _key_contains_any(key_normed, ("упаков", "предмет", "штук", "шт")):
        return "pack_count"
    if _key_contains_any(key_normed, ("оригиналь", "расходник")):
        return "original_consumable"
    if _key_contains_any(key_normed, ("совместим",)):
        return "compatible_models"
    if _key_contains_any(key_normed, ("двусторон",)):
        return "duplex"
    if _key_contains_any(key_normed, ("скорост",)) and _key_contains_any(key_normed, ("печати", "стр/мин")):
        return "print_speed_ppm"
    if _key_contains_any(key_normed, ("секретност",)):
        return "security_level"
    if _key_contains_any(key_normed, ("объем", "обьем")) and _key_contains_any(key_normed, ("корзин", "контейнер")):
        return "bin_volume_l"
    if _key_contains_any(key_normed, ("толщин",)) and _key_contains_any(key_normed, ("пленк", "ламинир")):
        return "laminating_thickness_microns"
    if _key_contains_any(key_normed, ("мощност",)):
        return "power_w"
    if _key_contains_any(key_normed, ("объем", "обьем")) and not _key_contains_any(key_normed, ("корзин", "контейнер", "накоп", "жестк", "ssd")):
        return "capacity_l"
    if _key_contains_any(key_normed, ("класс энерг", "энергоэффектив", "энергопотреблен")):
        return "energy_class"
    if _key_contains_any(key_normed, ("загрузк", "нагрузк")) and _key_contains_any(key_normed, ("макс", "белье", "кг")):
        return "load_kg"
    if _key_contains_any(key_normed, ("скорост",)) and _key_contains_any(key_normed, ("передач", "wan", "пропуск")):
        return "speed_mbps"
    if _key_contains_any(key_normed, ("мегапиксел", "разрешен")) and _key_contains_any(key_normed, ("матриц", "камер", "основ")):
        return "megapixels"
    if _key_contains_any(key_normed, ("цветов", "температур")) and _key_contains_any(key_normed, ("свет", "тон")):
        return "color_temperature_k"
    return None


def _resolve_field(raw_key: str) -> tuple[str | None, float]:
    key_normed = _nrm(raw_key).replace("ё", "е")
    direct = CHAR_KEY_ALIASES.get(key_normed)
    if direct is not None:
        return direct, 0.97

    heuristic = _heuristic_key_field(key_normed)
    if heuristic is not None:
        return heuristic, 0.82

    match = process.extractOne(
        key_normed,
        CHAR_KEY_ALIASES.keys(),
        scorer=fuzz.WRatio,
        score_cutoff=_KEY_MATCH_CUTOFF,
    )
    if not match:
        return None, 0.0
    alias, score, _ = match
    return CHAR_KEY_ALIASES[alias], min(0.9, score / 100)


def _normalize_field_value(field: str, raw_value: str) -> ScalarAttribute | None:
    normalizer = _FIELD_NORMALIZERS.get(field)
    if normalizer is not None:
        result = normalizer(raw_value)
        return result if result is not None else None

    cleaned = raw_value.strip()
    if not cleaned:
        return None
    if field in ("brand", "model"):
        return _nrm(cleaned)
    if field in _STRING_FIELDS:
        return cleaned
    return cleaned


def _attr_confidence(field_confidence: float, value: ScalarAttribute) -> float:
    if isinstance(value, bool):
        value_confidence = 0.88
    elif isinstance(value, int | float):
        value_confidence = 0.96
    else:
        value_confidence = 0.9 if str(value).strip() else 0.0
    return round(min(field_confidence, value_confidence), 4)


def _put_attribute(
    out: dict[str, CanonicalAttribute],
    *,
    key: str,
    value: ScalarAttribute,
    raw_key: str,
    raw_value: str,
    confidence: float,
) -> None:
    attr = CanonicalAttribute(
        key=key,
        value=value,
        unit=_FIELD_UNITS.get(key),
        source_key=raw_key,
        source_value=raw_value,
        confidence=confidence,
    )
    existing = out.get(key)
    if existing is None or attr.confidence > existing.confidence:
        out[key] = attr


def canonical_fields(canonical: CanonicalProduct) -> dict[str, ScalarAttribute]:
    return {key: attr.value for key, attr in canonical.attributes.items()}


def canonicalize_characteristics(
    characteristics: dict[str, str],
    *,
    category: str | None = None,
) -> CanonicalProduct:
    """Build typed comparable attributes while preserving source provenance."""
    attributes: dict[str, CanonicalAttribute] = {}
    raw: dict[str, str] = {}
    extra: dict[str, str] = {}

    for raw_key, raw_value in characteristics.items():
        raw_value = (raw_value or "").strip()
        raw_key = str(raw_key).strip()
        if not raw_key or not raw_value:
            continue
        raw[raw_key] = raw_value

        field, field_confidence = _resolve_field(raw_key)
        if field is None:
            extra[raw_key] = raw_value
            continue

        if field == "_manufacturer_color":
            extra["manufacturer_color"] = raw_value
            continue

        if field == "_tyre_type":
            season = normalize_season_str(raw_value)
            if season is not None:
                _put_attribute(
                    attributes,
                    key="season",
                    value=season,
                    raw_key=raw_key,
                    raw_value=raw_value,
                    confidence=min(field_confidence, 0.85),
                )
            continue

        value = _normalize_field_value(field, raw_value)
        if value is None:
            extra[raw_key] = raw_value
            continue

        _put_attribute(
            attributes,
            key=field,
            value=value,
            raw_key=raw_key,
            raw_value=raw_value,
            confidence=_attr_confidence(field_confidence, value),
        )

    confidence = (
        round(sum(attr.confidence for attr in attributes.values()) / len(attributes), 4)
        if attributes else 0.0
    )
    return CanonicalProduct(
        category=category,
        attributes=attributes,
        raw=raw,
        extra=extra,
        confidence=confidence,
    )


def normalize_structured_characteristics(
    characteristics: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Normalise marketplace characteristic key/value pairs.

    Returns:
        (canonical_fields, extra_raw) where:
        - canonical_fields: {field_name: typed_value} ready to populate ProductAttributes
        - extra_raw: {raw_key: raw_value} for non-mapped keys, stored in ProductAttributes.extra
    """
    canonical = canonicalize_characteristics(characteristics)
    return dict(canonical_fields(canonical)), dict(canonical.extra)


__all__ = [
    "CHAR_KEY_ALIASES",
    "canonical_fields",
    "canonicalize_characteristics",
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
