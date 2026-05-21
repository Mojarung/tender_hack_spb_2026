"""Manual smoke test for the full search pipeline.

Usage:
    uv run python scripts/smoke_search.py "iphone 15 128"

Prints a per-source summary and the top 3 offers from each adapter.
Hits real marketplaces — use sparingly.
"""

from __future__ import annotations

import asyncio
import json
import sys

# Windows cmd default cp1251 cannot print '→' / '•' — force UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pricepulse.orchestrator.search import SearchOrchestrator


async def main(query: str, limit: int) -> None:
    orch = SearchOrchestrator()
    normalized, groups = await orch.run(query=query, max_per_source=limit)

    print(f"\nQuery: {normalized.raw}  →  normalized: '{normalized.normalized}'")
    print("=" * 78)

    for g in groups:
        head = f"[{g.source.value.upper():<10}] count={g.count}  min={g.min_price}"
        if g.error:
            head += f"  ERROR: {g.error[:60]}"
        print(head)
        for o in g.offers[:3]:
            print(f"  • {o.price:>9}₽  {o.name[:62]}")
            print(f"            {o.url}")
        print("-" * 78)

    payload = {
        "query": normalized.model_dump(),
        "groups": [g.model_dump(mode="json") for g in groups],
    }
    out_path = "smoke_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nFull JSON: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: smoke_search.py <query> [limit]")
        sys.exit(2)
    q = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    asyncio.run(main(q, n))
