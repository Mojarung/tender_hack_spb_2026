"""Two-tier typo correction.

Tier 1 (Latin / brand tokens):
    RapidFuzz against the brand thesaurus (`thesaurus.ALL_TERMS`). This
    is what catches "iphonе" (cyrillic 'е'), "марашалл" → "marshall",
    "ксяоми" → "xiaomi" — short list, exact-ish matches.

Tier 2 (Russian general words):
    SymSpell + pymorphy3 from `ru_typos`. Handles "плинтер" → "принтер",
    "картрижд" → "картридж", "шипованая" → "шипованная" etc. — anything
    in the top-80k Russian frequency list plus our domain vocabulary.

Order matters: brand tokens get checked first because the thesaurus
encodes the right canonical form ("айфон" → "iphone"), which the general
Russian dictionary doesn't know about.
"""

from __future__ import annotations

from rapidfuzz import fuzz, process

from .ru_typos import correct_token as _ru_correct_token
from .thesaurus import ALL_TERMS

_BRAND_SCORE_CUTOFF = 80
_MIN_LEN = 4   # 'sony' (4) is correctable, 'ps' (2) is not


def _is_correctable(tok: str) -> bool:
    return len(tok) >= _MIN_LEN and not tok.isdigit()


def _brand_correct(tok: str) -> tuple[str, int]:
    """RapidFuzz against the brand thesaurus. Returns (best-or-orig, score)."""
    if not _is_correctable(tok):
        return tok, 0
    match = process.extractOne(
        tok, ALL_TERMS, scorer=fuzz.ratio, score_cutoff=_BRAND_SCORE_CUTOFF,
    )
    if match is None:
        return tok, 0
    best, score, _ = match
    return best, int(score)


def correct_token(tok: str, candidates: tuple[str, ...] = ALL_TERMS) -> tuple[str, int]:
    """Legacy signature: returns (best-or-orig, fuzzy-score).

    Kept for backward compatibility with callers / tests that exercised
    the brand-only behaviour. The `candidates` argument is honoured.
    """
    if not _is_correctable(tok):
        return tok, 0
    match = process.extractOne(
        tok, candidates, scorer=fuzz.ratio, score_cutoff=_BRAND_SCORE_CUTOFF,
    )
    if match is None:
        return tok, 0
    best, score, _ = match
    return best, int(score)


def correct_phrase(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Per-token correction across both tiers.

    For each token:
      1. Try the brand thesaurus first — high-confidence canonicalisation.
      2. If no brand hit, try the SymSpell Russian dictionary.
    Returns the corrected phrase and an audit list of (orig, fixed) pairs.
    """
    fixes: list[tuple[str, str]] = []
    out: list[str] = []
    for tok in text.split():
        brand_fix, brand_score = _brand_correct(tok)
        if brand_fix != tok and brand_score >= _BRAND_SCORE_CUTOFF:
            fixes.append((tok, brand_fix))
            out.append(brand_fix)
            continue

        ru_fix, ru_dist = _ru_correct_token(tok)
        if ru_fix != tok and ru_dist > 0:
            fixes.append((tok, ru_fix))
            out.append(ru_fix)
            continue

        out.append(tok)
    return " ".join(out), fixes


__all__ = ["correct_phrase", "correct_token"]
