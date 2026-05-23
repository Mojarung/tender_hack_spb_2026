"""Cascading anti-bot fallback strategy (L1 → L2 → L3).

Methodology compliance (final_presa.pdf, p.5 — «полный запрет на любые
внешние API»): the previous L3 (Scrapfly / Apify / ZenRows) and L5 (paid
2Captcha) layers are dropped. Every layer the router can escalate to
runs on our own infrastructure:

  L1: ``curl_cffi`` HTTP impersonation. Free, ~milliseconds per request.
  L2: ``nodriver`` stealth browser. Free; CPU + RAM.
  L3: local CAPTCHA solving — OpenCV slider solver + Gemma 4 VLM. Free.

Each layer has a circuit-breaker: the router escalates the next request
for an affected source after `fail_threshold` failures within
`fail_window_s` seconds, capped at :attr:`CascadeRouter.max_layer`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from time import time

from pricepulse.domain.enums import SourceKind


class Layer(IntEnum):
    L1_HTTP_IMPERSONATE = 1
    L2_STEALTH_BROWSER = 2
    L3_CAPTCHA_LOCAL = 3   # OpenCV slider + Gemma 4 VLM


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
        max_layer: Layer = Layer.L3_CAPTCHA_LOCAL,
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
    """Stateful router — pick the cheapest viable layer per source."""

    def __init__(self) -> None:
        self._state: dict[SourceKind, CascadeState] = {
            s: CascadeState() for s in SourceKind
        }

    @property
    def max_layer(self) -> Layer:
        return Layer.L3_CAPTCHA_LOCAL

    def layer_for(self, source: SourceKind) -> Layer:
        return self._state[source].current

    def record_outcome(self, source: SourceKind, layer: Layer, ok: bool) -> None:
        self._state[source].record(layer, ok, max_layer=self.max_layer)

    def reset(self, source: SourceKind) -> None:
        self._state[source] = CascadeState()
