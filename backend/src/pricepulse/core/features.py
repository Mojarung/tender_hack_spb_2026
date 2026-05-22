"""Operational feature flags.

The original surface gated several external APIs (2Captcha, Scrapfly,
Gemini, …). All those services were removed wholesale in compliance with
the hackathon methodology (final_presa.pdf p.5 — «полный запрет на
любые внешние API»), so this dataclass now carries only ``demo_mode`` —
the hint the cache warmer uses to pre-fill Redis for the jury demo.
"""

from __future__ import annotations

from dataclasses import dataclass

from pricepulse.config import Settings


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    demo_mode: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> FeatureFlags:
        return cls(demo_mode=settings.demo_mode)

    def summary(self) -> dict[str, bool]:
        return {"demo_mode": self.demo_mode}
