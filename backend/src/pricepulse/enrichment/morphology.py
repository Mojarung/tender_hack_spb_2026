"""Russian lemmatization via pymorphy3.

pymorphy3 is the maintained successor to pymorphy2 — a dictionary-based
morphological analyzer. It is lightweight (pure-Python DAWG reader +
a few-MB dictionary, no torch) and resolves a word in well under a
millisecond once the analyzer is built.

We use it for one job: collapse query tokens to their normal form so the
synonym thesaurus can be keyed by lemma — "наушниках" / "наушники" /
"наушником" all map to "наушник".

The analyzer is a lazily-built process-wide singleton (construction loads
the dictionary, ~0.2-0.5s). If pymorphy3 is unavailable the module
degrades to identity lemmatization — exact word forms still match.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

_morph: Any = None
_unavailable = False


def _analyzer() -> Any:
    global _morph, _unavailable
    if _morph is not None or _unavailable:
        return _morph
    try:
        import pymorphy3

        _morph = pymorphy3.MorphAnalyzer()
    except Exception as exc:  # missing dict / install — degrade gracefully
        log.warning("morphology.pymorphy3_unavailable", error=str(exc))
        _unavailable = True
        return None
    return _morph


def lemma(word: str) -> str:
    """Normal form of a single Russian word (lowercased).

    Falls back to the lowercased input when pymorphy3 is unavailable.
    """
    w = word.lower().strip()
    analyzer = _analyzer()
    if analyzer is None or not w:
        return w
    parsed = analyzer.parse(w)
    if not parsed:
        return w
    return str(parsed[0].normal_form)


def lemmatize(text: str) -> list[str]:
    """Lemmatize every whitespace-separated token of `text`."""
    return [lemma(tok) for tok in text.split()]


__all__ = ["lemma", "lemmatize"]
