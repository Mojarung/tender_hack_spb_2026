"""RU brand thesaurus for `normalize_query`.

Strategy: the marketplaces we hit (WB / Ozon / Я.Маркет / Runet) are
Russian and index Russian descriptions as primary. So we DO NOT
translate generic nouns like "наушники", "пылесос", "кроссовки" — they
already work and translating them hurts recall.

We only canonicalise brand names that consumers spell phonetically:
"айфон" → "iphone", "сяоми" → "xiaomi", etc. Both the Latin and the
cyrillic phonetic spellings live as keys for fuzzy correction.
"""

from __future__ import annotations

# Per-token brand normalisations (cyrillic → canonical Latin form).
TRANSLIT: dict[str, str] = {
    # phones / tablets — Apple
    "айфон": "iphone", "айфона": "iphone", "айфоны": "iphone",
    "айфоне": "iphone", "айфонов": "iphone", "айфончик": "iphone",
    "iphonе": "iphone",       # cyrillic 'е' typo
    "айпад": "ipad", "айпэд": "ipad",

    # phones / tablets — Android
    "самсунг": "samsung", "самсунга": "samsung",
    "сяоми": "xiaomi", "ксяоми": "xiaomi", "ксиоми": "xiaomi", "шаоми": "xiaomi",
    "хуавей": "huawei", "хуавэй": "huawei",
    "хонор": "honor",
    "редми": "redmi",
    "оппо": "oppo",

    # laptops
    "макбук": "macbook", "макбука": "macbook",
    "асус": "asus",
    "леново": "lenovo",
    "хп": "hp",
    "делл": "dell",

    # audio brands
    "сони": "sony",
    "бозе": "bose",
    "маршалл": "marshall", "марашалл": "marshall",
    "джбл": "jbl",
    "эйрподс": "airpods", "аирподс": "airpods", "аирпадс": "airpods",

    # apparel / shoes brands
    "найк": "nike",
    "адидас": "adidas",
    "пума": "puma",
    "рибок": "reebok",
    "нью бэлэнс": "new balance",

    # consoles / games — brand-ish acronyms
    "плейстейшн": "playstation", "плойка": "playstation",
    "пс5": "playstation 5", "ps5": "playstation 5",
    "пс4": "playstation 4", "ps4": "playstation 4",
    "иксбокс": "xbox", "хбокс": "xbox",
    "свитч": "nintendo switch",
    "нинтендо": "nintendo",
}

# Phrase aliases — applied BEFORE the per-token pass so multi-word
# brands win over single-word fixes ("айфон 15 про" → "iphone 15 pro").
PHRASES: dict[str, str] = {
    "айфон 15 про макс":  "iphone 15 pro max",
    "айфон 16 про макс":  "iphone 16 pro max",
    "айфон 15 про":       "iphone 15 pro",
    "айфон 16 про":       "iphone 16 pro",
    "айфон 15":           "iphone 15",
    "айфон 16":           "iphone 16",
    "макбук эйр":         "macbook air",
    "макбук эир":         "macbook air",
    "макбук про":         "macbook pro",
    "эйрподс про":        "airpods pro",
    "эйрподс макс":       "airpods max",
    "иксбокс сериес":     "xbox series",
}

# Flat list for fuzzy lookups (rapidfuzz candidates).
ALL_TERMS: tuple[str, ...] = tuple(
    sorted(set(list(TRANSLIT) + list(TRANSLIT.values()) + list(PHRASES)))
)


def translate(text: str) -> str:
    """Apply phrase + per-token brand translit. Leaves generic RU words alone."""
    t = text.lower()
    for phrase in sorted(PHRASES, key=len, reverse=True):
        if phrase in t:
            t = t.replace(phrase, PHRASES[phrase])
    out: list[str] = []
    for tok in t.split():
        out.append(TRANSLIT.get(tok, tok))
    return " ".join(out)


__all__ = ["ALL_TERMS", "PHRASES", "TRANSLIT", "translate"]
