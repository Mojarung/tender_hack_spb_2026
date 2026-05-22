"""Unit tests for the anti-bot cascade router (antibot/cascade.py)."""

from pricepulse.antibot.cascade import CascadeRouter, Layer
from pricepulse.core.features import FeatureFlags
from pricepulse.domain.enums import SourceKind

_OZON = SourceKind.OZON


def _flags(*, allow_paid: bool = False, use_2captcha: bool = False) -> FeatureFlags:
    return FeatureFlags(
        allow_paid=allow_paid,
        use_paid_proxies=False,
        use_2captcha=use_2captcha,
        use_paid_llm=False,
        use_paid_l3=False,
        demo_mode=False,
        cost_cap_usd=0,
    )


def _fail(router: CascadeRouter, source: SourceKind, n: int) -> None:
    """Record `n` failures at whatever layer the source currently sits on."""
    for _ in range(n):
        router.record_outcome(source, router.layer_for(source), ok=False)


def test_starts_at_l1() -> None:
    router = CascadeRouter(_flags())
    assert router.layer_for(_OZON) is Layer.L1_HTTP_IMPERSONATE


def test_escalates_after_three_failures() -> None:
    router = CascadeRouter(_flags())
    _fail(router, _OZON, 3)
    assert router.layer_for(_OZON) is Layer.L2_STEALTH_BROWSER


def test_two_failures_do_not_escalate() -> None:
    router = CascadeRouter(_flags())
    _fail(router, _OZON, 2)
    assert router.layer_for(_OZON) is Layer.L1_HTTP_IMPERSONATE


def test_success_keeps_layer() -> None:
    router = CascadeRouter(_flags())
    for _ in range(10):
        router.record_outcome(_OZON, router.layer_for(_OZON), ok=True)
    assert router.layer_for(_OZON) is Layer.L1_HTTP_IMPERSONATE


def test_free_mode_caps_at_l4() -> None:
    router = CascadeRouter(_flags())
    assert router.max_layer is Layer.L4_CAPTCHA_LOCAL
    _fail(router, _OZON, 30)
    assert router.layer_for(_OZON) is Layer.L4_CAPTCHA_LOCAL


def test_paid_captcha_unlocks_l5() -> None:
    router = CascadeRouter(_flags(allow_paid=True, use_2captcha=True))
    assert router.max_layer is Layer.L5_CAPTCHA_PAID
    _fail(router, _OZON, 30)
    assert router.layer_for(_OZON) is Layer.L5_CAPTCHA_PAID


def test_reset_returns_to_l1() -> None:
    router = CascadeRouter(_flags())
    _fail(router, _OZON, 3)
    assert router.layer_for(_OZON) is Layer.L2_STEALTH_BROWSER
    router.reset(_OZON)
    assert router.layer_for(_OZON) is Layer.L1_HTTP_IMPERSONATE


def test_sources_escalate_independently() -> None:
    router = CascadeRouter(_flags())
    _fail(router, _OZON, 3)
    assert router.layer_for(SourceKind.WB) is Layer.L1_HTTP_IMPERSONATE
