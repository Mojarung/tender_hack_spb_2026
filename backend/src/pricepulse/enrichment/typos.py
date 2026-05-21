"""Lightweight typo correction via RapidFuzz.

Why not SymSpell: it wants a multi-megabyte word-frequency dictionary
for every language we touch (RU + EN). Our domain is narrow — product
terms — so a fuzzy match against the curated thesaurus is enough and
needs no external data.

Threshold tuned at 86 to avoid swapping legitimate brand tokens (e.g.
"sony" is not "song"). Numbers and short tokens (<=2 chars) pass
through untouched.
"""

from __future__ import annotations

from rapidfuzz import fuzz, process

from .thesaurus import ALL_TERMS

_SCORE_CUTOFF = 80
_MIN_LEN = 4   # 'sony' (4) is correctable, 'ps' (2) is not


def _is_correctable(tok: str) -> bool:
    return len(tok) >= _MIN_LEN and not tok.isdigit()


def correct_token(tok: str, candidates: tuple[str, ...] = ALL_TERMS) -> tuple[str, int]:
    """Return (best-match-or-original, score). 100 = exact, 0 = no change."""
    if not _is_correctable(tok):
        return tok, 0
    match = process.extractOne(tok, candidates, scorer=fuzz.ratio, score_cutoff=_SCORE_CUTOFF)
    if match is None:
        return tok, 0
    best, score, _ = match
    return best, int(score)


def correct_phrase(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Per-token typo correction. Returns (corrected text, [(orig, fixed), ...])."""
    fixes: list[tuple[str, str]] = []
    out: list[str] = []
    for tok in text.split():
        fixed, score = correct_token(tok)
        if fixed != tok and score >= _SCORE_CUTOFF:
            fixes.append((tok, fixed))
            out.append(fixed)
        else:
            out.append(tok)
    return " ".join(out), fixes


__all__ = ["correct_token", "correct_phrase"]
