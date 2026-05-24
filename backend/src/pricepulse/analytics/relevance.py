"""Query-to-offer similarity, 0-100.

Used to rank results by how closely each offer matches what the user
actually typed — independent of price or trust signals. The full
`offers` list is then sortable by either «лучшая цена» (existing
best-deal score) or «по соответствию» (this score).

Pipeline:
  1. Build a "haystack" string from the offer's name plus a short
     selection of human-relevant characteristics (brand, model,
     memory, screen size, etc.) — full chars dump is too noisy.
  2. Tokenise both query and haystack: lowercase, strip punctuation,
     drop stopwords ("и", "в", "для", …) and pure-digit/spec dust.
  3. Run rapidfuzz.fuzz.token_set_ratio — robust to word order and
     missing words on either side.
  4. Bonus pass: every "spec token" in the query (256gb, r16, m1,
     pro, max …) that the haystack also contains adds 8 points,
     capped at +25. Spec tokens are what distinguishes "iPhone 15
     128GB" from "iPhone 15 256GB" — fuzz alone ranks both the same.
  5. Clamp to [0, 100].

The score is a soft signal — frontend renders it as a tiny "92 %
совпадение" pill, not a hard filter. Ranking by it is opt-in via the
sort dropdown.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# Russian + English stopwords that show up in queries / titles but
# carry no matching signal.
_STOPWORDS: frozenset[str] = frozenset({
    "и", "или", "в", "на", "для", "от", "до", "с", "со", "по", "за",
    "the", "a", "an", "of", "for", "with", "and", "or",
})

# Tokens that LOOK like specs (size, model, capacity) — used both to
# down-weight title noise and to credit exact spec matches.
_SPEC_TOKEN_RE = re.compile(
    r"^("
    r"\d+(?:[.,]\d+)?(?:[a-zа-я%/-]*)?"
    r"|[a-zа-я]\d+[a-zа-я0-9]*"
    r"|pro|max|plus|mini|ultra|lite|super"
    r")$",
    re.IGNORECASE,
)

# Sensible characteristics to fold into the haystack (keys may match
# substrings, case-insensitive). The catalog scrapers use different
# Russian labels for the same concept, so we keep the list loose.
_CHAR_INCLUDE = (
    "бренд", "марка", "модель", "серия", "линейка",
    "диагональ", "память", "озу", "ram", "процессор",
    "цвет", "материал", "вес", "размер", "тип", "категория",
    "состояние", "версия", "поколение",
)


def _tokenise(text: str) -> list[str]:
    # Keep cyrillic + latin + digits; everything else is a separator.
    raw = re.findall(r"[a-zа-яё0-9]+", text.lower())
    return [t for t in raw if t and t not in _STOPWORDS and len(t) > 1]


def _pick_chars(characteristics: dict[str, str]) -> list[str]:
    if not characteristics:
        return []
    out: list[str] = []
    for k, v in characteristics.items():
        if v is None or not str(v).strip():
            continue
        kl = k.lower()
        if any(needle in kl for needle in _CHAR_INCLUDE):
            out.append(str(v))
    return out


def _spec_bonus(query_tokens: list[str], haystack_tokens: set[str]) -> float:
    spec_q = [t for t in query_tokens if _SPEC_TOKEN_RE.match(t)]
    if not spec_q:
        return 0.0
    matched = sum(1 for t in spec_q if t in haystack_tokens)
    return min(25.0, matched * 8.0)


def relevance_score(
    query: str,
    name: str,
    characteristics: dict[str, str] | None = None,
) -> float:
    """Return a 0-100 similarity between the query and the offer."""
    q = (query or "").strip()
    n = (name or "").strip()
    if not q or not n:
        return 0.0

    q_tokens = _tokenise(q)
    if not q_tokens:
        return 0.0

    chars_text = " ".join(_pick_chars(characteristics or {}))
    haystack = f"{n} {chars_text}".strip()
    haystack_tokens = set(_tokenise(haystack))

    # token_set_ratio handles word reordering + extra words on either
    # side, which matches catalog titles ("Apple iPhone 15 (128 ГБ),
    # синий" vs "iphone 15 128gb").
    base = fuzz.token_set_ratio(q, haystack)
    bonus = _spec_bonus(q_tokens, haystack_tokens)
    score = base + bonus
    if score < 0:
        return 0.0
    if score > 100:
        return 100.0
    return round(score, 1)


__all__ = ["relevance_score"]
