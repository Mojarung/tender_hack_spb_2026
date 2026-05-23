"""Unit tests for the anti-bot cascade router (antibot/cascade.py)."""

from pricepulse.antibot.cascade import CascadeRouter, Layer
from pricepulse.domain.enums import SourceKind

_OZON = SourceKind.OZON


def _fail(router: CascadeRouter, source: SourceKind, n: int) -> None:
    """Record `n` failures at whatever layer the source currently sits on."""
    for _ in range(n):
        router.record_outcome(source, router.layer_for(source), ok=False)


def test_starts_at_l1() -> None:
    router = CascadeRouter()
    assert router.layer_for(_OZON) is Layer.L1_HTTP_IMPERSONATE


def test_escalates_after_three_failures() -> None:
    router = CascadeRouter()
    _fail(router, _OZON, 3)
    assert router.layer_for(_OZON) is Layer.L2_STEALTH_BROWSER


def test_two_failures_do_not_escalate() -> None:
    router = CascadeRouter()
    _fail(router, _OZON, 2)
    assert router.layer_for(_OZON) is Layer.L1_HTTP_IMPERSONATE


def test_success_keeps_layer() -> None:
    router = CascadeRouter()
    for _ in range(10):
        router.record_outcome(_OZON, router.layer_for(_OZON), ok=True)
    assert router.layer_for(_OZON) is Layer.L1_HTTP_IMPERSONATE


def test_caps_at_l3_local_captcha() -> None:
    router = CascadeRouter()
    assert router.max_layer is Layer.L3_CAPTCHA_LOCAL
    _fail(router, _OZON, 30)
    assert router.layer_for(_OZON) is Layer.L3_CAPTCHA_LOCAL


def test_reset_returns_to_l1() -> None:
    router = CascadeRouter()
    _fail(router, _OZON, 3)
    assert router.layer_for(_OZON) is Layer.L2_STEALTH_BROWSER
    router.reset(_OZON)
    assert router.layer_for(_OZON) is Layer.L1_HTTP_IMPERSONATE


def test_sources_escalate_independently() -> None:
    router = CascadeRouter()
    _fail(router, _OZON, 3)
    assert router.layer_for(SourceKind.WB) is Layer.L1_HTTP_IMPERSONATE
