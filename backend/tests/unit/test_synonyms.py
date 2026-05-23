"""Unit tests for lemmatization + synonym expansion."""

from __future__ import annotations

from pricepulse.enrichment.morphology import lemma
from pricepulse.enrichment.normalize import normalize_query
from pricepulse.enrichment.synonym_thesaurus import synonym_alternates


def test_lemmatize_collapses_word_forms() -> None:
    # pymorphy3 is a hard dependency — inflected forms collapse to one lemma.
    assert lemma("наушниках") == lemma("наушники") == lemma("наушником")


def test_synonym_alternate_for_known_term() -> None:
    alternates, notes = synonym_alternates("беспроводные наушники")
    assert any("гарнитура" in a for a in alternates)
    assert any("синоним" in n for n in notes)


def test_synonyms_match_any_word_form() -> None:
    # an inflected query word must still hit the thesaurus via its lemma
    assert synonym_alternates("ищу наушники")[0]
    assert synonym_alternates("обзор наушников")[0]


def test_no_alternates_for_unknown_word() -> None:
    alternates, notes = synonym_alternates("стол")
    assert alternates == []
    assert notes == []


def test_alternates_are_capped() -> None:
    alternates, _ = synonym_alternates("телефон ноутбук наушники", max_alternates=2)
    assert len(alternates) <= 2


async def test_normalize_query_populates_alternates() -> None:
    n = await normalize_query("наушники")
    assert any("гарнитура" in a for a in n.alternates)
    assert any("синоним" in e for e in n.expansions)


async def test_nofix_skips_synonyms() -> None:
    n = await normalize_query("наушники", fix=False)
    assert n.alternates == []
