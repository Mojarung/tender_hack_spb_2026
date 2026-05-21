"""Russian-language sentiment analysis for product reviews.

Model: `seara/rubert-tiny2-russian-sentiment` (12M params, ~3 ms/text on CPU).
3 classes: positive / neutral / negative.

Loaded lazily on first use; in production the lifespan hook in `main.py`
should warm the model up at startup to avoid cold-start latency on the
first API call.
"""

from functools import lru_cache
from typing import Literal

SentimentLabel = Literal["positive", "neutral", "negative"]


@lru_cache(maxsize=1)
def _pipeline():
    # TODO (hackathon): from transformers import pipeline; return pipeline(
    #     "sentiment-analysis",
    #     model="seara/rubert-tiny2-russian-sentiment",
    #     device=-1,   # CPU
    # )
    return None


async def classify(text: str) -> tuple[SentimentLabel, float]:
    """Return (label, score). Stub returns neutral until the pipeline is wired."""
    # pipe = _pipeline()
    # result = pipe(text[:512], truncation=True)[0]  # the model has a 512-token cap
    # return result["label"].lower(), result["score"]
    return "neutral", 0.0


__all__ = ["SentimentLabel", "classify"]
