"""Query normalization: cleanup → typo fix → brand translit → synonyms.

Pipeline:

  1. ``_clean`` — Unicode NFKC, lowercase, strip punctuation, collapse spaces.
  2. ``correct_phrase`` — RapidFuzz vs the brand thesaurus (~60 tokens) —
     catches phonetic brand typos ("айвон" → "айфон").
  3. **SpellCheck** — SAGE FRED-T5 distilled-95M (RuSpellRU F1 = 78.9)
     via the local ``spellcheck-svc`` microservice (``backend/spellcheck/``).
     Disabled when ``SPELLCHECK_URL`` is empty; degraded silently if the
     service is down.
  4. ``translate`` — RU brand → canonical Latin form ("айфон" → "iphone").
  5. ``synonym_alternates`` — alternate query strings via the curated
     thesaurus + pymorphy3 lemmatisation.

The whole result is **cached in Redis by raw query** when the caller
passes a ``RedisCache`` — the SAGE inference (~500 ms on CPU) is the
dominant cost in the pipeline, so a repeated query is essentially free.

Marketplace adapters search for ``NormalizedQuery.normalized``;
``alternates[]`` is the orchestrator's retry list; ``expansions[]`` is
the human-readable audit trail the API + UI surface.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

import structlog

from pricepulse.cache.redis_cache import RedisCache
from pricepulse.domain.models import NormalizedQuery
from pricepulse.enrichment.llm_query import llm_fix_query, llm_synonyms
from pricepulse.enrichment.spellcheck_client import SpellCheckClient
from pricepulse.enrichment.synonym_thesaurus import synonym_alternates
from pricepulse.enrichment.thesaurus import translate
from pricepulse.enrichment.typos import correct_phrase

log = structlog.get_logger(__name__)

_WHITESPACE = re.compile(r"\s+")
_PUNCT_KEEP = re.compile(r"[^\w\s\-+./ёЁ]", flags=re.UNICODE)

# Normalisation is deterministic per code version — long TTL is safe;
# bumping the schema is done by changing the cache-key version below.
_CACHE_TTL_S = 24 * 3600
_CACHE_VERSION = "v1"


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _PUNCT_KEEP.sub(" ", text.lower())
    return _WHITESPACE.sub(" ", text).strip()


def _cache_key(raw: str, fix: bool) -> str:
    h = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"normalize:{_CACHE_VERSION}:{int(fix)}:{h}"


async def normalize_query(
    raw: str,
    *,
    fix: bool = True,
    spellcheck: SpellCheckClient | None = None,
    cache: RedisCache | None = None,
) -> NormalizedQuery:
    """`fix=False` bypasses typo + translit + synonyms (search raw text).

    `cache`, when provided, short-circuits the whole pipeline on a hit —
    SAGE inference is ~500 ms, so repeated queries return in <2 ms."""

    # Cache lookup BEFORE _clean: we cache by raw input so the user's
    # exact string keys the entry.
    key = _cache_key(raw, fix) if cache is not None else None
    if key is not None:
        try:
            cached = await cache.get(key)
        except Exception as exc:  # cache never breaks the request
            log.debug("normalize.cache_get_failed", error=str(exc))
            cached = None
        if cached:
            return NormalizedQuery.model_validate(cached)

    cleaned = _clean(raw)
    if not cleaned or not fix:
        result = NormalizedQuery(raw=raw, normalized=cleaned)
        await _maybe_store(cache, key, result)
        return result

    notes: list[str] = []

    # 1) Brand typo correction (RapidFuzz over the brand thesaurus).
    text, brand_fixes = correct_phrase(cleaned)
    for orig, repl in brand_fixes:
        notes.append(f"опечатка: {orig} → {repl}")

    # 2) SpellCheck — SAGE FRED-T5 distilled, general RU spelling.
    #    Fallback to Gemma (Ollama Cloud) when SAGE is unavailable.
    sc = spellcheck if spellcheck is not None else SpellCheckClient()
    if sc.enabled:
        fixed = await sc.fix(text)
        if fixed and fixed != text:
            notes.append(f"опечатка: «{text}» → «{fixed}»")
            text = fixed
    else:
        # LLM query optimizer: transliteration, colloquial→product terms, brand normalization.
        # Runs unconditionally when SAGE is off — Gemma is fast enough on Cloud.
        fixed, llm_notes = await llm_fix_query(text)
        if fixed != text:
            notes.extend(llm_notes)
            text = fixed

    # 3) RU → EN canonical brand form ("айфон 15" → "iphone 15").
    translated = translate(text)
    if translated != text:
        notes.append(f"перевод: «{text}» → «{translated}»")
    text = translated

    # 4) Synonym alternates — curated thesaurus first, LLM fallback when empty.
    alternates, syn_notes = synonym_alternates(text)
    if not alternates:
        alternates = await llm_synonyms(text)

    result = NormalizedQuery(
        raw=raw,
        normalized=text,
        expansions=notes + syn_notes,
        alternates=alternates,
    )
    await _maybe_store(cache, key, result)
    return result


async def _maybe_store(
    cache: RedisCache | None, key: str | None, result: NormalizedQuery,
) -> None:
    if cache is None or key is None:
        return
    try:
        await cache.set(
            key, result.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_S,
        )
    except Exception as exc:  # cache never breaks the request
        log.debug("normalize.cache_set_failed", error=str(exc))
