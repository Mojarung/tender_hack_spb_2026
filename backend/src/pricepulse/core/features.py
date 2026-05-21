"""Feature flags — free-mode is the default; paid services are opt-in.

The global `FEATURES_ALLOW_PAID` is a killswitch. Even if granular flags
(`FEATURE_USE_PAID_PROXIES`, etc.) are true, paid branches are only taken
when the killswitch is also true.

Usage:
    if features.paid_captcha_enabled:
        await two_captcha.solve(...)
    else:
        await gemma4_vlm.solve(...)
"""

from __future__ import annotations

from dataclasses import dataclass

from pricepulse.config import Settings


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    allow_paid: bool
    use_paid_proxies: bool
    use_2captcha: bool
    use_paid_llm: bool
    use_paid_l3: bool
    demo_mode: bool
    cost_cap_usd: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "FeatureFlags":
        return cls(
            allow_paid=settings.features_allow_paid,
            use_paid_proxies=settings.feature_use_paid_proxies,
            use_2captcha=settings.feature_use_2captcha,
            use_paid_llm=settings.feature_use_paid_llm,
            use_paid_l3=settings.feature_use_paid_l3,
            demo_mode=settings.demo_mode,
            cost_cap_usd=settings.cost_cap_usd,
        )

    # Effective gates — both global killswitch AND granular flag AND key present
    @property
    def paid_proxies_enabled(self) -> bool:
        return self.allow_paid and self.use_paid_proxies

    @property
    def paid_captcha_enabled(self) -> bool:
        return self.allow_paid and self.use_2captcha

    @property
    def paid_llm_enabled(self) -> bool:
        return self.allow_paid and self.use_paid_llm

    @property
    def paid_l3_enabled(self) -> bool:
        return self.allow_paid and self.use_paid_l3

    def summary(self) -> dict[str, bool | int]:
        return {
            "allow_paid": self.allow_paid,
            "paid_proxies": self.paid_proxies_enabled,
            "paid_captcha": self.paid_captcha_enabled,
            "paid_llm": self.paid_llm_enabled,
            "paid_l3": self.paid_l3_enabled,
            "demo_mode": self.demo_mode,
            "cost_cap_usd": self.cost_cap_usd,
        }
