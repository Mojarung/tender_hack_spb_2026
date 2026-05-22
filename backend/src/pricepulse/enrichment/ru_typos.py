"""Russian-aware typo correction.

Pipeline per token:
    1. If the token is already a known Russian word (in our dictionary)
       or its lemma is — bail out, leave it alone.
    2. Ask SymSpell for the best candidate at edit-distance ≤ 2.
    3. If a candidate exists and is meaningfully better (higher freq,
       different spelling) — return it.

Implementation notes:
    * SymSpell index is built lazily on first use from `wordfreq.top_n_list`
      (top 80k Russian forms) + boosted domain vocabulary from
      ``enrichment.categories``. ~20 МБ RAM, ~1.5 s warmup, then <0.3 ms/lookup.
    * pymorphy3 handles inflection so we don't accept "футболке" but reject
      "футболка" — both share the lemma "футболка".
    * Singletons survive across requests via ``functools.cache``.
"""

from __future__ import annotations

import logging
import re
from functools import cache, lru_cache

from pricepulse.enrichment.categories import ALL_DOMAIN, DOMAIN_FREQ

log = logging.getLogger(__name__)

# Tokens shorter than this never get corrected (too risky).
_MIN_LEN = 4
# Tokens that look numeric / size-coded ("205", "55", "r16") pass through.
_NUM_RE = re.compile(r"^[\dxXхХrR/.\-+]+$")
# Maximum SymSpell edit distance — 2 is the usual sweet spot for RU.
_MAX_EDIT_DISTANCE = 2


def _is_numeric_token(tok: str) -> bool:
    return bool(_NUM_RE.match(tok))


def _is_correctable(tok: str) -> bool:
    if len(tok) < _MIN_LEN:
        return False
    if _is_numeric_token(tok):
        return False
    # Cyrillic-only — Latin words go through the brand-thesaurus path.
    return any("а" <= ch <= "я" or ch == "ё" for ch in tok)


@cache
def _get_symspell():
    """Build (lazily) a SymSpell instance from wordfreq + domain vocab."""
    from symspellpy import SymSpell

    sym = SymSpell(max_dictionary_edit_distance=_MAX_EDIT_DISTANCE, prefix_length=7)

    # Domain words first — high boost so they always win ties.
    for term in ALL_DOMAIN:
        sym.create_dictionary_entry(term.lower(), DOMAIN_FREQ)

    # Brand-translit keys — register them as known words so that
    # "самсунг" / "айфон" don't get rewritten into a similarly-spelled
    # general Russian noun by SymSpell. The brand pipeline runs later
    # in `typos.correct_phrase` and translates them to the Latin form.
    from pricepulse.enrichment.thesaurus import TRANSLIT
    for brand_key in TRANSLIT:
        sym.create_dictionary_entry(brand_key.lower(), DOMAIN_FREQ)

    # General Russian frequency dictionary (top 80k Zipf-ranked forms).
    try:
        from wordfreq import top_n_list, zipf_frequency

        for word in top_n_list("ru", 80_000):
            # Skip junk / single chars.
            if len(word) < 2:
                continue
            z = zipf_frequency(word, "ru")
            # SymSpell stores integer frequencies — convert Zipf to raw count.
            count = int(10 ** z)
            if count <= 0:
                continue
            sym.create_dictionary_entry(word, count)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully if wordfreq fails
        log.warning("ru_typos.wordfreq_load_failed: %s", exc)

    return sym


@cache
def _get_morph():
    """Lazy pymorphy3 — used to detect already-valid inflected forms."""
    from pymorphy3 import MorphAnalyzer
    return MorphAnalyzer()


@lru_cache(maxsize=4096)
def _is_known_word(tok: str) -> bool:
    """True if `tok` (or its lemma) appears in our dictionary."""
    sym = _get_symspell()
    if tok in sym.words:
        return True
    morph = _get_morph()
    for parse in morph.parse(tok)[:3]:
        if parse.normal_form in sym.words:
            return True
    return False


@lru_cache(maxsize=4096)
def correct_token(tok: str) -> tuple[str, int]:
    """Return (best-candidate-or-original, edit-distance).

    Edit distance 0 means "no change". Caller can decide whether to
    surface the correction to the user based on the distance.
    """
    if not _is_correctable(tok):
        return tok, 0
    if _is_known_word(tok):
        return tok, 0

    from symspellpy import Verbosity

    sym = _get_symspell()
    suggestions = sym.lookup(
        tok,
        Verbosity.TOP,
        max_edit_distance=_MAX_EDIT_DISTANCE,
        include_unknown=False,
        transfer_casing=False,
    )
    if not suggestions:
        return tok, 0
    best = suggestions[0]
    if best.term == tok or best.distance == 0:
        return tok, 0
    # Reject corrections that change the token too aggressively for short words.
    if len(tok) <= 5 and best.distance > 1:
        return tok, 0
    return best.term, int(best.distance)


def correct_phrase(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Per-token correction. Returns (corrected_text, [(orig, fixed), ...])."""
    fixes: list[tuple[str, str]] = []
    out: list[str] = []
    for tok in text.split():
        fixed, dist = correct_token(tok)
        if fixed != tok and dist > 0:
            fixes.append((tok, fixed))
            out.append(fixed)
        else:
            out.append(tok)
    return " ".join(out), fixes


__all__ = ["correct_token", "correct_phrase"]
