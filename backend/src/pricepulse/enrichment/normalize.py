"""Query normalization: cleanup → typo fix → brand translit → synonyms.

`NormalizedQuery.normalized` is the single best query string the
marketplace adapters search for. `alternates[]` carries synonym-swapped
variants the orchestrator retries when a source comes back empty.
`expansions[]` is a human-readable audit trail for the API + UI.
"""

from __future__ import annotations

import re
import unicodedata

from pricepulse.domain.models import NormalizedQuery
from pricepulse.enrichment.synonym_thesaurus import synonym_alternates
from pricepulse.enrichment.synonyms import expand

_WHITESPACE = re.compile(r"\s+")
_PUNCT_KEEP = re.compile(r"[^\w\s\-+./ёЁ]", flags=re.UNICODE)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _PUNCT_KEEP.sub(" ", text.lower())
    return _WHITESPACE.sub(" ", text).strip()


async def normalize_query(raw: str, *, fix: bool = True) -> NormalizedQuery:
    """`fix=False` bypasses typo + translit + synonyms (search raw text)."""
    cleaned = _clean(raw)
    if not cleaned or not fix:
        return NormalizedQuery(raw=raw, normalized=cleaned)
    canonical, notes = expand(cleaned)
    alternates, syn_notes = synonym_alternates(canonical)
    return NormalizedQuery(
        raw=raw,
        normalized=canonical,
        expansions=notes + syn_notes,
        alternates=alternates,
    )
