"""Synonym + transliteration expansion.

Pipeline:
    raw    → cleanup  (strip punct, lowercase)
    clean  → typo fix (rapidfuzz vs thesaurus)
    fixed  → translit (RU→EN canonical form for marketplace search)

The function exposes both forms so the orchestrator can record the
typo correction in `NormalizedQuery.expansions[]` and the frontend can
show it back to the user ("искали 'айвон 15' → 'iphone 15'").
"""

from __future__ import annotations

from .thesaurus import translate
from .typos import correct_phrase


def expand(text: str) -> tuple[str, list[str]]:
    """Return (best_query_for_marketplaces, expansions_for_audit).

    `expansions` is human-readable: e.g. ["typo: айвон → iphone", "ru→en: айфон → iphone"].
    """
    notes: list[str] = []

    fixed, fixes = correct_phrase(text)
    for orig, repl in fixes:
        notes.append(f"опечатка: {orig} → {repl}")

    translated = translate(fixed)
    if translated != fixed:
        notes.append(f"перевод: «{fixed}» → «{translated}»")

    return translated, notes


__all__ = ["expand"]
