"""Unit tests for the JamSpell client + its integration into normalize_query."""

from __future__ import annotations

import httpx
import respx

from pricepulse.enrichment.jamspell_client import JamSpellClient
from pricepulse.enrichment.normalize import normalize_query

_TEST_URL = "http://jamspell-test:8080"


def test_client_disabled_when_url_is_empty() -> None:
    assert JamSpellClient(url="").enabled is False


def test_client_enabled_when_url_present() -> None:
    assert JamSpellClient(url=_TEST_URL).enabled is True


async def test_fix_returns_none_when_disabled() -> None:
    assert await JamSpellClient(url="").fix("anything") is None


async def test_fix_returns_none_for_empty_text() -> None:
    client = JamSpellClient(url=_TEST_URL)
    assert await client.fix("") is None
    assert await client.fix("   ") is None


@respx.mock
async def test_fix_returns_corrected_text() -> None:
    respx.post(f"{_TEST_URL}/fix").mock(
        return_value=httpx.Response(
            200, json={"original": "наушникки", "fixed": "наушники"},
        ),
    )
    client = JamSpellClient(url=_TEST_URL)
    assert await client.fix("наушникки") == "наушники"


@respx.mock
async def test_fix_returns_none_on_http_5xx() -> None:
    respx.post(f"{_TEST_URL}/fix").mock(return_value=httpx.Response(500))
    assert await JamSpellClient(url=_TEST_URL).fix("anything") is None


@respx.mock
async def test_fix_returns_none_on_connect_error() -> None:
    respx.post(f"{_TEST_URL}/fix").mock(
        side_effect=httpx.ConnectError("no route"),
    )
    assert await JamSpellClient(url=_TEST_URL).fix("anything") is None


@respx.mock
async def test_normalize_query_applies_jamspell_correction() -> None:
    respx.post(f"{_TEST_URL}/fix").mock(
        return_value=httpx.Response(
            200,
            json={
                "original": "беспроводные наушникки",
                "fixed": "беспроводные наушники",
            },
        ),
    )
    n = await normalize_query(
        "беспроводные наушникки",
        jamspell=JamSpellClient(url=_TEST_URL),
    )
    assert "наушники" in n.normalized
    assert any("наушникки" in note for note in n.expansions)


async def test_normalize_query_skips_jamspell_when_disabled() -> None:
    # JamSpell disabled → brand thesaurus alone does not know "наушникки",
    # so the text passes through unchanged.
    n = await normalize_query("наушникки", jamspell=JamSpellClient(url=""))
    assert n.normalized == "наушникки"


@respx.mock
async def test_normalize_query_unchanged_when_jamspell_unreachable() -> None:
    # jamspell-svc is "configured" but the network drops every call.
    respx.post(f"{_TEST_URL}/fix").mock(
        side_effect=httpx.ConnectError("nope"),
    )
    n = await normalize_query(
        "наушникки",
        jamspell=JamSpellClient(url=_TEST_URL),
    )
    assert n.normalized == "наушникки"
    assert not any(note.startswith("опечатка: «") for note in n.expansions)
