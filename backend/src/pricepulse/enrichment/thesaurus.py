"""RU↔EN product-term thesaurus.

Curated by hand — covers the categories we expect to see on a price-
search hackathon demo. Keys MUST be lowercase. The right-hand side is
the canonical form we'd rather send to a marketplace (English usually
gives better recall on WB/Ozon because their search index treats Latin
brand tokens as primary).

Add a row, get instant coverage for:
  - direct lookup ("айфон" → "iphone")
  - fuzzy match ("айвон" → "iphone" via rapidfuzz)
"""

from __future__ import annotations

# Single-token translit (used for substitution inside multi-word queries).
TRANSLIT: dict[str, str] = {
    # phones / tablets
    "айфон": "iphone",
    "айфона": "iphone",
    "айфоны": "iphone",
    "айфоне": "iphone",
    "iphonе": "iphone",   # cyrillic 'е' typo
    "айпад": "ipad",
    "айпэд": "ipad",
    "самсунг": "samsung",
    "ксиоми": "xiaomi",
    "сяоми": "xiaomi",
    "хуавей": "huawei",
    # laptops
    "макбук": "macbook",
    "макбука": "macbook",
    "ноутбук": "ноутбук",   # works fine in RU on WB
    # audio
    "наушники": "наушники",
    "беспроводные наушники": "wireless headphones",
    "колонка": "speaker",
    "сони": "sony",
    "бозе": "bose",
    "марашалл": "marshall",
    "маршалл": "marshall",
    # home appliances
    "пылесос": "vacuum",
    "робот пылесос": "robot vacuum",
    "робот-пылесос": "robot vacuum",
    "кофемашина": "coffee machine",
    "кофеварка": "coffee maker",
    "блендер": "blender",
    "мультиварка": "multicooker",
    "стиралка": "стиральная машина",
    "сушилка": "сушильная машина",
    # peripherals
    "клавиатура": "keyboard",
    "клава": "keyboard",
    "мышка": "mouse",
    "мышь": "mouse",
    "монитор": "monitor",
    "веб камера": "webcam",
    # apparel / shoes
    "кроссовки": "sneakers",
    "кроссы": "sneakers",
    "найк": "nike",
    "адидас": "adidas",
    "пума": "puma",
    "рибок": "reebok",
    # consoles / games
    "плойка": "playstation",
    "плейстейшн": "playstation",
    "ps5": "playstation 5",
    "пс5": "playstation 5",
    "xbox": "xbox",
    "иксбокс": "xbox",
    "свитч": "nintendo switch",
    "нинтендо": "nintendo",
}

# Full-phrase aliases — substitution wins over per-token translit.
PHRASES: dict[str, str] = {
    "айфон 15": "iphone 15",
    "айфон 15 про": "iphone 15 pro",
    "айфон 15 про макс": "iphone 15 pro max",
    "айфон 16": "iphone 16",
    "макбук эир": "macbook air",
    "макбук про": "macbook pro",
    "робот пылесос": "robot vacuum",
    "беспроводные наушники": "wireless headphones",
    "наушники сони": "sony headphones",
    "иксбокс сериес": "xbox series",
}

# Convenience flat list for fuzzy lookups.
ALL_TERMS: tuple[str, ...] = tuple(
    sorted(set(list(TRANSLIT) + list(TRANSLIT.values()) + list(PHRASES)))
)


def translate(text: str) -> str:
    """RU→EN substitution. Tries longest phrase match first, then word."""
    t = text.lower()
    # Phrase pass — longest first so 'айфон 15 про' wins over 'айфон 15'.
    for phrase in sorted(PHRASES, key=len, reverse=True):
        if phrase in t:
            t = t.replace(phrase, PHRASES[phrase])
    # Per-token translit.
    out: list[str] = []
    for tok in t.split():
        out.append(TRANSLIT.get(tok, tok))
    return " ".join(out)


__all__ = ["TRANSLIT", "PHRASES", "ALL_TERMS", "translate"]
