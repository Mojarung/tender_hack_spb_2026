"""Integration test of the FULL ranking pipeline against live marketplaces.

Hits Yandex Market (WB rate-limits us in tight loops, Ozon needs L2). Runs the
real SearchOrchestrator end-to-end, then prints top_deals with their explain
fields (deal_score, relevance_score, match/mismatch/unknown signals).

Run from `backend/`:
    uv run python scripts/test_real_extraction.py
"""

from __future__ import annotations

import asyncio
import sys

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import ProductAttributes
from pricepulse.orchestrator.search import SearchOrchestrator
from pricepulse.scrapers.yandex_market import YandexMarketScraper


QUERIES: list[str] = [
    "iphone 15 pro 256 черный",
    "iphone 15 pro",                # low-spec — should still rank iPhones over cases
    "ноутбук asus 16/512",
    "бумага A4 80 г/м2 500 листов",
    "шины зимние 205/55 R16",
    "картридж HP 12A",
]


def _attrs_dump(attrs: ProductAttributes | None) -> str:
    if attrs is None:
        return "—"
    data = {
        k: v for k, v in attrs.model_dump().items()
        if v not in (None, "", {}, 0.0) and k not in ("raw", "extra")
    }
    return ", ".join(f"{k}={v}" for k, v in data.items())[:100]


async def run() -> None:
    out_path = "scripts/real_extraction_result.txt"
    out = open(out_path, "w", encoding="utf-8")

    def log(msg: str) -> None:
        print(msg)
        out.write(msg + "\n")
        out.flush()

    log("=" * 110)
    log("REAL-DATA RANKING TEST — full orchestrator pipeline, no LLM")
    log("=" * 110)

    # Yandex-only orchestrator: WB/Ozon need delays/L2 — skipping for clean signal.
    orch = SearchOrchestrator(
        adapters={SourceKind.YA_MARKET: YandexMarketScraper(timeout_s=15.0)},
    )

    for query in QUERIES:
        log("")
        log(f"━━━ QUERY: {query!r}")
        try:
            normalized, groups, top_deals = await orch.run(
                query, max_per_source=10, sources=[SourceKind.YA_MARKET],
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  ERROR: {exc}")
            continue

        qa = normalized.attributes
        log(f"    query.attrs: {_attrs_dump(qa)}")
        log(f"    query.conf:  {qa.confidence:.2f}" if qa else "    query.conf: —")
        total_offers = sum(g.count for g in groups)
        log(f"    fetched: {total_offers} offers across sources")
        log("")
        if not top_deals:
            log("    <no top deals>")
            continue

        log("    TOP DEALS (after filter + composite ranking):")
        for d in top_deals[:8]:
            o = d.offer
            log(
                f"      #{d.rank} score={d.score:+.4f}  rel={d.relevance_score:.2f}  "
                f"deal={d.deal_score:+.4f}  price={o.price}  src={o.source.value}"
            )
            log(f"        title: {o.name[:100]}")
            log(f"        attrs: {_attrs_dump(o.attributes)}")
            signals = []
            if d.match_signals:
                signals.append(f"matched={d.match_signals}")
            if d.mismatch_signals:
                signals.append(f"MISmatch={d.mismatch_signals}")
            if d.unknown_signals:
                signals.append(f"unknown={d.unknown_signals}")
            if signals:
                log(f"        explain: {'  |  '.join(signals)}")

    log(f"\nFull report: {out_path}")
    out.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run())
