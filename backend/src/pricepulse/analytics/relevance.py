"""Query-to-offer similarity, 0-100.

Used to rank results by how closely each offer matches what the user
typed AND how trustworthy the listing is. The combined score lets the
"По соответствию" sort do double duty: it surfaces exact matches first,
and within ties prefers offers with strong rating × review-count.

Pipeline:
  1. Build a "haystack" string from the offer's name plus a short
     selection of human-relevant characteristics (brand, model,
     memory, screen size, etc.) — full chars dump is too noisy.
  2. Tokenise both query and haystack: lowercase, strip punctuation,
     drop stopwords ("и", "в", "для", …) and pure-digit/spec dust.
  3. Run rapidfuzz.fuzz.token_set_ratio — robust to word order and
     missing words on either side.
  4. Spec-token bonus: every "spec token" in the query (256gb, r16,
     m1, pro, max …) that the haystack also contains adds 8 points,
     capped at +25. Spec tokens distinguish "iPhone 15 128GB" from
     "iPhone 15 256GB" — fuzz alone ranks both the same.
  5. Trust bonus: log10(reviews + 1) × rating, normalised to a
     up-to-+10-point boost. Applied ONLY when base text similarity
     ≥ 30 so a popular irrelevant item can't bubble up past a
     specific match. Scales with the base score so weak matches get
     a smaller absolute boost.
  6. Clamp to [0, 100].

The score is a soft signal — frontend renders it as a tiny "92 %
совпадение" pill and uses it as the default sort key.
"""

from __future__ import annotations

import math
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


def _trust_bonus(base_text: float, rating: float | None, reviews_count: int | None) -> float:
    """Up to +10 points for a popular, highly-rated listing — applied
    only when the underlying text match is at least vaguely relevant
    (≥30). Scales with the base score so a barely-relevant card with
    5000 reviews doesn't get artificially pushed over a perfectly
    matched one with few reviews."""
    if base_text < 30.0:
        return 0.0
    r = float(rating) if rating is not None else 4.0
    n = float(reviews_count or 0)
    trust = math.log10(n + 1.0) * r          # 0..~20 in practice
    trust_norm = min(1.0, trust / 15.0)      # 0..1
    # Scale by base/100 — a 100-match gets the full +10, a 30-match
    # gets at most +3. Keeps text relevance as the dominant signal.
    return trust_norm * 10.0 * (base_text / 100.0)


def relevance_score(
    query: str,
    name: str,
    characteristics: dict[str, str] | None = None,
    *,
    rating: float | None = None,
    reviews_count: int | None = None,
) -> float:
    """Return a 0-100 similarity between the query and the offer.
    Combines fuzzy text match, exact spec-token bonus and a trust
    bonus from rating × review-count."""
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
    base = float(fuzz.token_set_ratio(q, haystack))
    spec = _spec_bonus(q_tokens, haystack_tokens)
    trust = _trust_bonus(base, rating, reviews_count)
    score = base + spec + trust
    if score < 0:
        return 0.0
    if score > 100:
        return 100.0
    return round(score, 1)


__all__ = ["relevance_score"]
