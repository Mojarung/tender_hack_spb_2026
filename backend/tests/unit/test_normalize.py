"""Cover the typo-fix + RU→EN translit pipeline behind `normalize_query`."""

from __future__ import annotations

import pytest

from pricepulse.enrichment.normalize import normalize_query


@pytest.mark.asyncio
async def test_clean_passthrough_for_plain_query() -> None:
    n = await normalize_query("nothing special")
    assert n.normalized == "nothing special"
    assert n.expansions == []


@pytest.mark.asyncio
async def test_ru_to_en_phrase_translation() -> None:
    n = await normalize_query("Айфон 15 Про")
    assert n.normalized == "iphone 15 pro"
    assert any("перевод" in note for note in n.expansions)


@pytest.mark.asyncio
async def test_ru_to_en_single_token() -> None:
    n = await normalize_query("кроссовки найк")
    assert n.normalized == "sneakers nike"


@pytest.mark.asyncio
async def test_typo_correction_simple() -> None:
    n = await normalize_query("айвон 15")
    # rapidfuzz pulls "айвон" → "айфон" (1 char away, ratio≈90)
    # then translit RU→EN → "iphone 15"
    assert n.normalized == "iphone 15"
    assert any("опечатка" in note for note in n.expansions)


@pytest.mark.asyncio
async def test_short_tokens_not_corrected() -> None:
    n = await normalize_query("ps 5")
    # ps is < 3 chars → no fuzzy mangling
    assert n.normalized.startswith("ps")


@pytest.mark.asyncio
async def test_cleanup_strips_punctuation_and_lowercases() -> None:
    n = await normalize_query("  iPhone 15!!  ")
    assert n.normalized == "iphone 15"
