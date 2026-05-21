"""Query normalization: cleanup → typo fix → synonym/translit expansion.

End result: `NormalizedQuery.normalized` is what the marketplace
adapters actually search for, while `expansions[]` carries an audit
trail the API + UI can show to explain the change.
"""

from __future__ import annotations

import re
import unicodedata

from pricepulse.domain.models import NormalizedQuery
from pricepulse.enrichment.synonyms import expand

_WHITESPACE = re.compile(r"\s+")
_PUNCT_KEEP = re.compile(r"[^\w\s\-+./ёЁ]", flags=re.UNICODE)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _PUNCT_KEEP.sub(" ", text.lower())
    return _WHITESPACE.sub(" ", text).strip()


async def normalize_query(raw: str, *, fix: bool = True) -> NormalizedQuery:
    """`fix=False` bypasses typo + translit (search raw as user typed)."""
    cleaned = _clean(raw)
    if not cleaned or not fix:
        return NormalizedQuery(raw=raw, normalized=cleaned, expansions=[])
    canonical, notes = expand(cleaned)
    return NormalizedQuery(raw=raw, normalized=canonical, expansions=notes)
