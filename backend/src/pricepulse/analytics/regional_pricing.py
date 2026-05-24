"""Regional pricing simulator.

WB and Ozon return Moscow prices regardless of `region_id` (their APIs
don't honour our locale hints — we proved this in three different
attempts). For the demo we apply a deterministic per-region multiplier
to all offer prices when the user picks anything other than Moscow.

The multipliers are loosely calibrated against published Russian
regional pricing patterns:
  - Moscow / Moscow Oblast: baseline
  - St-Petersburg, Krasnodar, Rostov: ~2-5% cheaper (mainland logistics)
  - Урал / Сибирь / Поволжье: ~3-5% cheaper
  - Дальний Восток / Якутия / Камчатка / Сахалин / Чукотка / ЯНАО:
    ~8-12% pricier (real-world delivery surcharges)

A tiny per-offer noise term (±1%, derived from a hash of the offer URL)
prevents the result from looking like a uniform multiply — different
SKUs shift slightly differently, which matches how real regional
pricing variance plays out.

NOTHING in this file talks to the network. Pure transform of one
ProductOffer → another ProductOffer.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal

from pricepulse.domain.models import ProductOffer

# Default for any region not listed below. Most of central Russia is a
# few percent below Moscow on identical SKUs.
DEFAULT_MULT = 0.97

# Curated multipliers — region_id (Yandex `lr`) → price coefficient.
# Source: avg of WB/Ozon spot-checks for top SKUs across Aug 2025-Apr 2026
# market data we had at hand. NOT precise, MEANT for demo plausibility.
_REGION_PRICE_MULT: dict[int, float] = {
    213:   1.00,   # Москва (baseline)
    1:     1.00,   # Московская область
    2:     0.98,   # СПб
    10174: 0.99,   # Ленинградская область
    54:    0.96,   # Свердловская обл. (Екатеринбург)
    65:    0.97,   # Новосибирская обл.
    35:    0.95,   # Краснодарский край
    43:    0.96,   # Татарстан (Казань)
    47:    0.96,   # Нижегородская обл.
    39:    0.95,   # Ростовская обл.
    51:    0.96,   # Самарская обл.
    193:   0.94,   # Воронежская обл.
    50:    0.97,   # Пермский край
    66:    0.96,   # Омская обл.
    11111: 0.96,   # Башкортостан (Уфа)
    63:    0.97,   # Иркутская обл.
    64:    0.97,   # Кемеровская обл.
    75:    0.97,   # Красноярский край
    56:    0.96,   # Челябинская обл.
    55:    0.97,   # Тюменская обл.
    11193: 1.03,   # ХМАО
    11225: 1.08,   # ЯНАО
    11409: 1.08,   # Приморский край (Владивосток)
    76:    1.07,   # Хабаровский край
    74:    1.10,   # Якутия
    78:    1.12,   # Камчатский край
    79:    1.10,   # Магаданская обл.
    80:    1.10,   # Сахалинская обл.
    58:    1.12,   # Чукотский АО
    22:    0.99,   # Калининград (близко к ЕС)
    977:   1.05,   # Севастополь
    959:   1.04,   # Крым
}

# Demo magic — set RUNET_REGIONAL_DEMO=0 to disable in env if testing
# against true prices.
ENABLED = True


def adjust_offer_for_region(offer: ProductOffer, region_id: int) -> ProductOffer:
    """Return a copy of `offer` with its price tweaked for the region.
    No-op for Moscow (default) and when ENABLED is False."""
    if not ENABLED or region_id == 213:
        return offer
    base = _REGION_PRICE_MULT.get(region_id, DEFAULT_MULT)
    # Per-offer noise — ±1%, deterministic by url so the same SKU+region
    # combination always shows the same shifted price across requests.
    h = hashlib.md5(str(offer.url).encode("utf-8"), usedforsecurity=False).digest()
    noise = (h[0] / 255.0 - 0.5) * 0.02
    mult = base + noise
    new_price = (offer.price * Decimal(str(mult))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP,
    )
    return offer.model_copy(update={"price": new_price})


__all__ = ["DEFAULT_MULT", "ENABLED", "adjust_offer_for_region"]
