"""PricePulse MCP server — exposes the agent toolbox to external clients
(Claude Code, Cursor, our own chatbot, etc.) over the Model Context
Protocol.

Run with:
    uv run python -m pricepulse.agent.mcp_server     # stdio (CLI agents)
    uv run python -m pricepulse.agent.mcp_server http 8765 8050   # HTTP

The Streamable HTTP transport binds on port 8050 by default — use that
for Docker / network clients. The stdio transport is intended for
embedded CLI agents that spawn the server as a subprocess.
"""

from __future__ import annotations

import sys

from fastmcp import FastMCP

from pricepulse.agent import tools as t
from pricepulse.domain.enums import SourceKind

mcp = FastMCP(name="pricepulse")


@mcp.tool
async def search_products(query: str, max_per_source: int = 5) -> dict:
    """Search Wildberries, Ozon, Yandex Market and a floating 4th source
    for a product. Returns per-source groups + Best-Deal-ranked top
    offers. Use this when the user asks "where to buy X" or "find the
    cheapest X".
    """
    return await t.search_products(query, max_per_source=max_per_source)


@mcp.tool
async def get_top_deals(query: str, top_k: int = 5) -> list[dict]:
    """Return the top-ranked offers for a query, ordered by Best-Deal
    Score. Lighter than `search_products` — good for "give me the top 3"
    style asks.
    """
    return await t.get_top_deals(query, top_k=top_k)


@mcp.tool
async def get_price_history(
    source: str, item_id: str, limit: int = 100,
) -> dict:
    """Return accumulated price history for a product. `source` is one
    of: wb, ozon, ya_market, runet. `item_id` is the source-native id
    (nm_id for WB)."""
    return await t.get_price_history(SourceKind(source), item_id, limit=limit)


@mcp.tool
async def get_reviews_sample(item_id: int, sample: int = 30) -> dict:
    """Fetch recent Wildberries feedbacks for `imt_id` (the `root` of
    a WB product) and return the sentiment breakdown with sample quotes
    grouped by positive / neutral / negative."""
    return await t.get_reviews_sample(item_id, sample=sample)


@mcp.tool
async def compare_offers(items: list[dict]) -> dict:
    """Compare prices of several items from different sources. Each
    item is `{"source": "...", "item_id": "..."}`. Returns the latest
    captured price for each + the cheapest among them."""
    return await t.compare_offers(items)


def _main() -> None:
    """CLI entrypoint. Supports `stdio` (default) and `http` transports."""
    args = sys.argv[1:]
    if not args or args[0] in ("stdio", "-"):
        mcp.run()
        return
    if args[0] == "http":
        port = int(args[1]) if len(args) >= 2 else 8050
        host = args[2] if len(args) >= 3 else "0.0.0.0"  # noqa: S104
        mcp.run(transport="http", host=host, port=port)
        return
    raise SystemExit(
        f"Unknown transport '{args[0]}'. Use 'stdio' or 'http [port [host]]'."
    )


if __name__ == "__main__":   # pragma: no cover
    _main()
