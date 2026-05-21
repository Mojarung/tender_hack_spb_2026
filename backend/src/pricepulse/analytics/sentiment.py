"""Russian-language sentiment analysis.

Model: `seara/rubert-tiny2-russian-sentiment` (12M params, 3 ms/text on CPU,
3 classes positive/neutral/negative). Loaded lazily — the first call pays
the ~5s warmup cost, subsequent calls are millisecond-scale.

`transformers` / `torch` live in the `nlp` optional extra (heavy ~1.5GB).
If they are not installed, `classify_batch` short-circuits to neutral so
the rest of the pipeline keeps working — `is_available()` lets callers
warn the user.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

SentimentLabel = Literal["positive", "neutral", "negative"]
_MODEL = "seara/rubert-tiny2-russian-sentiment"

_pipeline = None  # cached pipeline instance
_pipeline_ready: bool | None = None


def is_available() -> bool:
    """True if transformers + torch are importable."""
    global _pipeline_ready
    if _pipeline_ready is not None:
        return _pipeline_ready
    try:
        import torch        # noqa: F401
        import transformers # noqa: F401
        _pipeline_ready = True
    except ImportError:
        _pipeline_ready = False
    return _pipeline_ready


def _get_pipeline():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    from transformers import pipeline   # local import — heavy
    _pipeline = pipeline(
        "sentiment-analysis",
        model=_MODEL,
        device=-1,
        truncation=True,
        max_length=256,
    )
    log.info("sentiment.pipeline_warmed", model=_MODEL)
    return _pipeline


def _normalize_label(label: str) -> SentimentLabel:
    label = label.lower()
    if "pos" in label:
        return "positive"
    if "neg" in label:
        return "negative"
    return "neutral"


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()  # noqa: S324


@dataclass(frozen=True, slots=True)
class SentimentResult:
    label: SentimentLabel
    score: float


@dataclass(frozen=True, slots=True)
class SentimentBreakdown:
    total: int
    positive: int
    neutral: int
    negative: int
    positive_pct: float
    neutral_pct: float
    negative_pct: float


def empty_breakdown() -> SentimentBreakdown:
    return SentimentBreakdown(0, 0, 0, 0, 0.0, 0.0, 0.0)


def aggregate(results: list[SentimentResult]) -> SentimentBreakdown:
    total = len(results)
    if total == 0:
        return empty_breakdown()
    pos = sum(1 for r in results if r.label == "positive")
    neg = sum(1 for r in results if r.label == "negative")
    neu = total - pos - neg
    return SentimentBreakdown(
        total=total,
        positive=pos, neutral=neu, negative=neg,
        positive_pct=round(pos * 100 / total, 1),
        neutral_pct=round(neu * 100 / total, 1),
        negative_pct=round(neg * 100 / total, 1),
    )


async def classify_batch(
    texts: list[str],
    *,
    redis: "Redis | None" = None,
    cache_ttl_s: int = 24 * 3600,
) -> list[SentimentResult]:
    """Classify each text in `texts`. Skips empty strings (returns neutral)."""
    if not texts:
        return []
    if not is_available():
        log.warning("sentiment.unavailable", reason="nlp extra not installed")
        return [SentimentResult("neutral", 0.0) for _ in texts]

    results: list[SentimentResult | None] = [None] * len(texts)
    misses: list[tuple[int, str]] = []   # (idx, text)

    # Phase 1: Redis cache lookup
    if redis is not None:
        keys = [f"sentiment:rubert-tiny2:{_text_hash(t)}" for t in texts]
        try:
            cached = await redis.mget(keys)
        except Exception:  # noqa: BLE001
            cached = [None] * len(texts)
        for i, hit in enumerate(cached):
            if hit:
                try:
                    label, score = hit.decode("utf-8").split("|", 1)
                    results[i] = SentimentResult(_normalize_label(label), float(score))
                    continue
                except (ValueError, UnicodeDecodeError):
                    pass
            misses.append((i, texts[i]))
    else:
        misses = list(enumerate(texts))

    # Phase 2: inference for misses
    if misses:
        miss_texts = [t if t.strip() else "нейтрально" for _, t in misses]
        pipeline_ = _get_pipeline()
        raw_outputs = pipeline_(miss_texts)
        for (idx, _), out in zip(misses, raw_outputs, strict=True):
            results[idx] = SentimentResult(
                _normalize_label(out["label"]),
                float(out["score"]),
            )
        # Phase 3: write-back to cache
        if redis is not None:
            try:
                pipe = redis.pipeline()
                for (idx, text), res in zip(misses, [results[i] for i, _ in misses], strict=True):
                    pipe.set(
                        f"sentiment:rubert-tiny2:{_text_hash(text)}",
                        f"{res.label}|{res.score:.4f}",  # type: ignore[union-attr]
                        ex=cache_ttl_s,
                    )
                await pipe.execute()
            except Exception:  # noqa: BLE001
                pass

    return [r if r is not None else SentimentResult("neutral", 0.0) for r in results]


async def classify(text: str) -> SentimentResult:
    out = await classify_batch([text])
    return out[0]


__all__ = [
    "SentimentLabel",
    "SentimentResult",
    "SentimentBreakdown",
    "classify",
    "classify_batch",
    "aggregate",
    "empty_breakdown",
    "is_available",
]
