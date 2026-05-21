"""Fingerprint generation (UA, viewport, lang, sec-ch-ua, timezone)."""

import random
from dataclasses import dataclass

_VIEWPORTS = [(1920, 1080), (1536, 864), (1440, 900), (1366, 768)]
_TIMEZONES = ["Europe/Moscow", "Europe/Samara", "Asia/Yekaterinburg"]
_LANGS = ["ru-RU,ru;q=0.9,en;q=0.7", "ru-RU,ru;q=0.9"]
# A small set of recent realistic UA strings (May 2026). Rotate via BrowserForge
# at runtime for production.
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


@dataclass(slots=True)
class Fingerprint:
    user_agent: str
    viewport: tuple[int, int]
    timezone: str
    accept_language: str


def random_fingerprint(seed: int | None = None) -> Fingerprint:
    rng = random.Random(seed)
    return Fingerprint(
        user_agent=rng.choice(_UAS),
        viewport=rng.choice(_VIEWPORTS),
        timezone=rng.choice(_TIMEZONES),
        accept_language=rng.choice(_LANGS),
    )
