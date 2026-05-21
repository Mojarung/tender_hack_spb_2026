import re

from pricepulse.domain.models import NormalizedQuery

_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s\-+./]", flags=re.UNICODE)


async def normalize_query(raw: str) -> NormalizedQuery:
    """First-pass cleanup; lemmatization + typo fix come from `typos` and `synonyms`."""
    cleaned = _PUNCT.sub(" ", raw.lower())
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    # TODO: hook up symspell (`pricepulse.enrichment.typos`) and synonym dict
    # (`pricepulse.enrichment.synonyms`) once data is loaded at startup.
    return NormalizedQuery(raw=raw, normalized=cleaned, expansions=[])
