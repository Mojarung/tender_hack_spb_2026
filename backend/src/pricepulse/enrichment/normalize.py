"""Query normalization: cleanup → lemmatize → synonym/typo expand.

Hackathon scope: cleanup + light translit-aware lowercasing. Typo and
synonym expansion are wired but defer to dedicated modules — keep them
swappable so we can plug in symspell / pymorphy without touching call sites.
"""

from __future__ import annotations

import re
import unicodedata

from pricepulse.domain.models import NormalizedQuery

_WHITESPACE = re.compile(r"\s+")
_PUNCT_KEEP = re.compile(r"[^\w\s\-+./ёЁ]", flags=re.UNICODE)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _PUNCT_KEEP.sub(" ", text.lower())
    text = _WHITESPACE.sub(" ", text).strip()
    return text


async def normalize_query(raw: str) -> NormalizedQuery:
    cleaned = _clean(raw)
    # TODO: hook into pricepulse.enrichment.typos and ...synonyms once the
    # symspell dictionary is loaded at startup. Expansions stay empty until then.
    return NormalizedQuery(raw=raw, normalized=cleaned, expansions=[])
