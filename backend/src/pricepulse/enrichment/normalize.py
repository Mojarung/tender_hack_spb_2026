"""Query normalization: cleanup → typo fix → brand translit → synonyms.

Pipeline:

  1. ``_clean`` — Unicode NFKC, lowercase, strip punctuation, collapse spaces.
  2. ``correct_phrase`` — RapidFuzz vs the brand thesaurus (~60 tokens) —
     catches phonetic brand typos ("айвон" → "айфон").
  3. **JamSpell** — N-gram Russian spell-correction via the local
     ``jamspell-svc`` microservice (``backend/jamspell/``). Disabled when
     ``JAMSPELL_URL`` is empty; degraded silently if the service is down.
  4. ``translate`` — RU brand → canonical Latin form ("айфон" → "iphone").
  5. ``synonym_alternates`` — alternate query strings via the curated
     thesaurus + pymorphy3 lemmatisation.

Marketplace adapters search for ``NormalizedQuery.normalized``;
``alternates[]`` is the orchestrator's retry list; ``expansions[]`` is
the human-readable audit trail the API + UI surface.
"""

from __future__ import annotations

import re
import unicodedata

from pricepulse.domain.models import NormalizedQuery
from pricepulse.enrichment.jamspell_client import JamSpellClient
from pricepulse.enrichment.synonym_thesaurus import synonym_alternates
from pricepulse.enrichment.thesaurus import translate
from pricepulse.enrichment.typos import correct_phrase

_WHITESPACE = re.compile(r"\s+")
_PUNCT_KEEP = re.compile(r"[^\w\s\-+./ёЁ]", flags=re.UNICODE)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _PUNCT_KEEP.sub(" ", text.lower())
    return _WHITESPACE.sub(" ", text).strip()


async def normalize_query(
    raw: str,
    *,
    fix: bool = True,
    jamspell: JamSpellClient | None = None,
) -> NormalizedQuery:
    """`fix=False` bypasses typo + translit + synonyms (search raw text)."""
    cleaned = _clean(raw)
    if not cleaned or not fix:
        return NormalizedQuery(raw=raw, normalized=cleaned)

    notes: list[str] = []

    # 1) Brand typo correction (RapidFuzz over the brand thesaurus).
    text, brand_fixes = correct_phrase(cleaned)
    for orig, repl in brand_fixes:
        notes.append(f"опечатка: {orig} → {repl}")

    # 2) JamSpell — general RU spell-correction. No-op when JAMSPELL_URL
    #    is empty or the service is unreachable.
    js = jamspell if jamspell is not None else JamSpellClient()
    if js.enabled:
        fixed = await js.fix(text)
        if fixed and fixed != text:
            notes.append(f"опечатка: «{text}» → «{fixed}»")
            text = fixed

    # 3) RU → EN canonical brand form ("айфон 15" → "iphone 15").
    translated = translate(text)
    if translated != text:
        notes.append(f"перевод: «{text}» → «{translated}»")
    text = translated

    # 4) Synonym alternates.
    alternates, syn_notes = synonym_alternates(text)

    return NormalizedQuery(
        raw=raw,
        normalized=text,
        expansions=notes + syn_notes,
        alternates=alternates,
    )
