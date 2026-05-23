"""Best-Deal Score: composite ranking across offers from different sources.

Formula:
    score = w1 * price_z
          + w2 * rating
          + w3 * log(1 + reviews_count)
          + w4 * seller_trust
          - w5 * delivery_days

Where `price_z` is z-score of price within the result set (negative is good —
cheaper than median). Weights are configurable and exposed to the UI so the
jury can tweak them live during the demo.
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, stdev


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    price: float = 0.40
    rating: float = 0.25
    reviews: float = 0.15
    seller_trust: float = 0.15
    delivery: float = 0.05


def best_deal_score(
    price: Decimal,
    rating: float,
    reviews_count: int,
    seller_trust: float = 0.5,
    delivery_days: int = 3,
    *,
    price_population: list[Decimal] | None = None,
    weights: ScoringWeights | None = None,
) -> float:
    w = weights or ScoringWeights()
    population = price_population or [price]
    if len(population) >= 2:
        mu = mean(float(p) for p in population)
        sigma = stdev(float(p) for p in population) or 1.0
        price_z = (float(price) - mu) / sigma
    else:
        price_z = 0.0

    return (
        -w.price * price_z              # cheaper = higher score
        + w.rating * (rating / 5.0)
        + w.reviews * math.log1p(reviews_count) / 10.0
        + w.seller_trust * seller_trust
        - w.delivery * (delivery_days / 30.0)
    )


def composite_rank_score(deal_score: float, relevance: float, query_conf: float) -> float:
    """Combine deal_score and relevance into final ranking signal.

    - relevance is centered around 0.5 so that a "no info" query (relevance=0.5)
      contributes 0 — ranking falls back to deal_score.
    - The relevance weight grows with query_conf: confident queries lean heavily
      on attribute match; vague queries lean on price/rating.
    """
    qc = max(0.0, min(1.0, query_conf))
    w_rel = 0.6 + 0.2 * qc          # 0.60 .. 0.80
    w_deal = 1.0 - w_rel             # 0.40 .. 0.20
    return w_rel * (relevance - 0.5) + w_deal * deal_score
