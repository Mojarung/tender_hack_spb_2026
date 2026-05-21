"""Typo correction via SymSpell.

To be wired during the hackathon. Idea:
    - load a Russian frequency dictionary at app startup (lifespan),
    - expose `correct(token: str) -> str` and `correct_phrase(text: str) -> str`,
    - hook into `enrichment.normalize.normalize_query`.
"""
