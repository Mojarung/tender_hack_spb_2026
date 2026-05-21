"""Rotating proxy pool.

Two tiers:
  - residential — for Yandex Market and Ozon.
  - datacenter — for WB (lighter site) and Runet generic scraping.

Sticky session: same (proxy, UA) bound to a workflow for 5–15 minutes
so that DataDome / SmartCaptcha doesn't immediately re-challenge.
"""

import itertools
from dataclasses import dataclass


@dataclass(slots=True)
class ProxyChoice:
    uri: str
    tier: str  # "residential" | "datacenter"


class ProxyPool:
    def __init__(self, residential: list[str], datacenter: list[str]) -> None:
        self._residential = itertools.cycle(residential) if residential else None
        self._datacenter = itertools.cycle(datacenter) if datacenter else None

    def pick(self, tier: str) -> ProxyChoice | None:
        cycle = self._residential if tier == "residential" else self._datacenter
        if cycle is None:
            return None
        return ProxyChoice(uri=next(cycle), tier=tier)
