"""Manual smoke test for the search pipeline.

Usage:
    uv run python scripts/smoke_search.py "iphone 15 128"
    uv run python scripts/smoke_search.py "iphone 15 128" 3 --sources wb,ozon,ya_market

Prints a per-source summary and the top 3 offers from each adapter.
Hits real marketplaces - use sparingly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

# Windows cmd default cp1251 cannot print '→' / '•' — force UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pricepulse.orchestrator.search import SearchOrchestrator
from pricepulse.domain.enums import SourceKind


def _parse_sources(raw: str | None) -> list[SourceKind] | None:
    if not raw:
        return None
    mapping = {s.value: s for s in SourceKind}
    sources: list[SourceKind] = []
    for item in raw.split(","):
        name = item.strip()
        if not name:
            continue
        if name not in mapping:
            allowed = ", ".join(mapping)
            raise SystemExit(f"Unknown source '{name}'. Allowed: {allowed}")
        sources.append(mapping[name])
    return sources or None


async def main(query: str, limit: int, sources: list[SourceKind] | None) -> None:
    orch = SearchOrchestrator()
    normalized, groups, top_deals = await orch.run(
        query=query,
        max_per_source=limit,
        sources=sources,
    )

    print(f"\nQuery: {normalized.raw}  ->  normalized: '{normalized.normalized}'")
    if normalized.expansions:
        print("Expansions:", "; ".join(normalized.expansions))
    print("=" * 78)

    for g in groups:
        head = f"[{g.source.value.upper():<10}] count={g.count}  min={g.min_price}"
        if g.error:
            head += f"  ERROR: {g.error[:60]}"
        print(head)
        for o in g.offers[:3]:
            print(f"  - {o.price:>9} RUB  {o.name[:62]}")
            print(f"            {o.url}")
        print("-" * 78)

    if top_deals:
        print("Top deals:")
        for deal in top_deals[:3]:
            offer = deal.offer
            print(
                f"  #{deal.rank} score={deal.score} "
                f"[{offer.source.value}] {offer.price} RUB  {offer.name[:62]}"
            )

    payload = {
        "query": normalized.model_dump(),
        "groups": [g.model_dump(mode="json") for g in groups],
        "top_deals": [d.model_dump(mode="json") for d in top_deals],
    }
    out_path = "smoke_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nFull JSON: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("limit", nargs="?", type=int, default=5)
    parser.add_argument(
        "--sources",
        help="Comma-separated sources: wb,ozon,ya_market,runet",
    )
    args = parser.parse_args()
    asyncio.run(main(args.query, args.limit, _parse_sources(args.sources)))
