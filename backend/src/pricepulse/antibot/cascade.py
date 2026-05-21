"""Cascading anti-bot fallback strategy (L1 → L2 → L3 → L4).

Free-mode is the default — paid layers are skipped entirely unless feature
flags allow them. See backend/docs/free-mode.md.

  L1: curl_cffi (TLS impersonate) + free Oracle/WARP proxy.   $0 / req
  L2: Patchright / Camoufox + warm cookies.                   $0 / req (CPU+RAM)
  L3: third-party (Scrapfly/Apify/ZenRows). Free-tier always allowed;
      paid plans only when `paid_l3_enabled`.
  L4: local CAPTCHA (OpenCV slider + Gemma 4 VLM). $0.
       Paid fallback to 2Captcha for kaleidoscope only when
       `paid_captcha_enabled`.

Each layer has a circuit-breaker. The router escalates the next request
for the affected source after N consecutive failures within a window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from time import time

from pricepulse.core.features import FeatureFlags
from pricepulse.domain.enums import SourceKind


class Layer(IntEnum):
    L1_HTTP_IMPERSONATE = 1
    L2_STEALTH_BROWSER = 2
    L3_THIRD_PARTY = 3       # free-tier always, paid only behind feature flag
    L4_CAPTCHA_LOCAL = 4     # OpenCV + Gemma 4 (free)
    L5_CAPTCHA_PAID = 5      # 2Captcha, opt-in


@dataclass(slots=True)
class LayerStats:
    failures: list[float] = field(default_factory=list)
    successes: int = 0


@dataclass(slots=True)
class CascadeState:
    """Per-source escalation state."""

    current: Layer = Layer.L1_HTTP_IMPERSONATE
    stats: dict[Layer, LayerStats] = field(default_factory=dict)

    def record(
        self,
        layer: Layer,
        ok: bool,
        *,
        fail_window_s: float = 60.0,
        fail_threshold: int = 3,
        max_layer: Layer = Layer.L4_CAPTCHA_LOCAL,
    ) -> None:
        s = self.stats.setdefault(layer, LayerStats())
        if ok:
            s.successes += 1
            return
        now = time()
        s.failures = [t for t in s.failures if now - t < fail_window_s]
        s.failures.append(now)
        if len(s.failures) >= fail_threshold and self.current < max_layer:
            self.current = Layer(self.current + 1)


class CascadeRouter:
    """Stateful router that picks the cheapest viable layer per source,
    honoring feature flags so paid layers are unreachable in free-mode."""

    def __init__(self, flags: FeatureFlags) -> None:
        self._flags = flags
        self._state: dict[SourceKind, CascadeState] = {s: CascadeState() for s in SourceKind}

    @property
    def max_layer(self) -> Layer:
        """Top of the ladder we're allowed to climb to."""
        return Layer.L5_CAPTCHA_PAID if self._flags.paid_captcha_enabled else Layer.L4_CAPTCHA_LOCAL

    def layer_for(self, source: SourceKind) -> Layer:
        return self._state[source].current

    def record_outcome(self, source: SourceKind, layer: Layer, ok: bool) -> None:
        self._state[source].record(layer, ok, max_layer=self.max_layer)

    def reset(self, source: SourceKind) -> None:
        self._state[source] = CascadeState()

    def can_use_paid_l3(self) -> bool:
        return self._flags.paid_l3_enabled

    def can_use_paid_captcha(self) -> bool:
        return self._flags.paid_captcha_enabled

    def can_use_paid_llm(self) -> bool:
        return self._flags.paid_llm_enabled

    def can_use_paid_proxies(self) -> bool:
        return self._flags.paid_proxies_enabled
