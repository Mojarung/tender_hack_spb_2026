"""Prometheus metrics for the scraping pipeline.

Cardinality is kept bounded (bounded label sets only).
Do NOT add `proxy_ip`, `product_id`, `url` — those go to logs (Loki) instead.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------- per-scrape
scrape_requests_total = Counter(
    "scrape_requests_total",
    "Total scrape requests",
    ["source", "outcome", "proxy_tier"],
)

scrape_duration_seconds = Histogram(
    "scrape_duration_seconds",
    "Scrape duration",
    ["source"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60),
)

scrape_offers_returned_total = Counter(
    "scrape_offers_returned_total",
    "Offers parsed from a successful response",
    ["source"],
)

# ---------------------------------------------------------------- anti-bot
proxy_in_use = Gauge(
    "proxy_in_use",
    "Active proxy sessions by tier",
    ["tier"],   # residential | datacenter | mobile | free
)

captcha_solve_attempts_total = Counter(
    "captcha_solve_attempts_total",
    "Captcha solve attempts",
    ["source", "provider", "outcome"],   # provider: 2captcha | capmonster | capsolver | local
)

browser_pool_size = Gauge(
    "browser_pool_size",
    "Browser (Patchright/Camoufox) instances",
    ["source", "status"],   # idle | busy
)

# ---------------------------------------------------------------- cache / queue
cache_hits_total = Counter("cache_hits_total", "Redis cache hits", ["source"])
cache_misses_total = Counter("cache_misses_total", "Redis cache misses", ["source"])

arq_queue_length = Gauge("arq_queue_length", "Pending jobs", ["queue"])

# ---------------------------------------------------------------- cost
# Track in micro-USD (1e-6 USD) to keep Counter monotonic & integer-friendly.
# Divide by 1_000_000 in Grafana for USD.
scrape_cost_units_total = Counter(
    "scrape_cost_units_total",
    "Cost in micro-USD per request",
    ["source", "cost_type"],   # proxy | captcha | llm | api
)


__all__ = [
    "arq_queue_length",
    "browser_pool_size",
    "cache_hits_total",
    "cache_misses_total",
    "captcha_solve_attempts_total",
    "proxy_in_use",
    "scrape_cost_units_total",
    "scrape_duration_seconds",
    "scrape_offers_returned_total",
    "scrape_requests_total",
]
