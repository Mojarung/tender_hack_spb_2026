"""Sentiment analytics: works without transformers installed (returns
all-neutral), aggregate breakdown maths, picking quotes."""

from __future__ import annotations

import pytest

from pricepulse.analytics import sentiment as sa
from pricepulse.analytics.sentiment import (
    SentimentResult,
    aggregate,
    classify_batch,
    empty_breakdown,
)


@pytest.mark.asyncio
async def test_classify_batch_without_transformers_returns_all_neutral(monkeypatch) -> None:
    monkeypatch.setattr(sa, "_pipeline_ready", False)
    out = await classify_batch(["вещь огонь", "ужасно", ""])
    assert len(out) == 3
    assert all(r.label == "neutral" for r in out)


def test_aggregate_breakdown_percentages() -> None:
    results = [
        SentimentResult("positive", 0.9),
        SentimentResult("positive", 0.85),
        SentimentResult("neutral",  0.6),
        SentimentResult("negative", 0.7),
    ]
    b = aggregate(results)
    assert b.total == 4
    assert b.positive == 2
    assert b.neutral == 1
    assert b.negative == 1
    assert b.positive_pct == 50.0
    assert b.neutral_pct == 25.0
    assert b.negative_pct == 25.0


def test_empty_breakdown_is_all_zero() -> None:
    b = empty_breakdown()
    assert b.total == b.positive == b.neutral == b.negative == 0
    assert b.positive_pct == b.neutral_pct == b.negative_pct == 0.0
