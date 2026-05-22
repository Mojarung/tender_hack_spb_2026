"""Curated product-domain synonym thesaurus.

Why curated rather than RuWordNet / navec embeddings (both researched
2026-05-22):

* **RuWordNet** (`ruwordnet` package, 59 905 synsets) is a general-language
  thesaurus — it expands by *every* word sense, so "мышь" the rodent
  pollutes a "мышь" product search. It also needs a separate data download.
* **navec** (Natasha compact embeddings, ~50 MB) returns nearest neighbours
  that are merely *related* — "телефон" pulls "звонок", "связь" — not
  substitutable query terms.

For a bounded domain (consumer electronics + appliances) a hand-built set
of genuinely substitutable terms is more precise, predictable and has zero
data-download footprint. Groups are matched by **lemma**
(see enrichment/morphology.py), so any inflected word form hits.
"""

from __future__ import annotations

from pricepulse.enrichment.morphology import lemma

# Each tuple is a set of mutually substitutable product terms. Members are
# written in natural form; lookup is lemma-based so word forms still match.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("наушники", "гарнитура"),
    ("телефон", "смартфон", "мобильник"),
    ("ноутбук", "лэптоп"),
    ("телевизор", "тв"),
    ("компьютер", "пк"),
    ("колонка", "акустика"),
    ("мышь", "мышка"),
    ("кроссовки", "кеды"),
    ("кофеварка", "кофемашина"),
    ("монитор", "дисплей"),
    ("рюкзак", "ранец"),
    ("микроволновка", "свч"),
    ("видеокарта", "видюха"),
    ("планшет", "таблет"),
    ("кофемолка", "кофеизмельчитель"),
)

# Lazily built: lemma(member) -> the group it belongs to.
_table: dict[str, tuple[str, ...]] | None = None


def _lemma_table() -> dict[str, tuple[str, ...]]:
    global _table
    if _table is None:
        built: dict[str, tuple[str, ...]] = {}
        for group in _SYNONYM_GROUPS:
            for member in group:
                built[lemma(member)] = group
        _table = built
    return _table


def synonym_alternates(
    query: str, *, max_alternates: int = 3,
) -> tuple[list[str], list[str]]:
    """Return ``(alternate query strings, audit notes)``.

    For each query token that belongs to a synonym group, emit a variant
    query with that token swapped for a group synonym — one swap per
    variant, so there is no combinatorial blow-up. Capped at
    `max_alternates`.
    """
    tokens = query.split()
    if not tokens:
        return [], []
    table = _lemma_table()
    alternates: list[str] = []
    notes: list[str] = []
    seen = {query}
    for i, tok in enumerate(tokens):
        tok_lemma = lemma(tok)
        group = table.get(tok_lemma)
        if group is None:
            continue
        for syn in group:
            if lemma(syn) == tok_lemma:
                continue
            variant_tokens = list(tokens)
            variant_tokens[i] = syn
            variant = " ".join(variant_tokens)
            if variant in seen:
                continue
            seen.add(variant)
            alternates.append(variant)
            notes.append(f"синоним: {tok} → {syn}")
            if len(alternates) >= max_alternates:
                return alternates, notes
    return alternates, notes


__all__ = ["synonym_alternates"]
