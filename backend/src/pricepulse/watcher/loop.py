"""Background price-watch loop.

Single in-process asyncio task started in `main.lifespan`. Every
WATCHER_TICK_SEC seconds it scans `price_watches` for rows whose
`last_check_at` is older than `interval_min`, runs them through the
orchestrator one at a time (limit `WATCHER_PARALLEL` per tick so we
don't melt the stealth browser pool), and writes a `PriceAlert`
whenever the best offer's price moved more than `threshold_pct`.

Why an in-process loop, not arq:
    * Hackathon timeline — adding worker infra costs more than the
      feature is worth.
    * The orchestrator already owns the heavy browsers; running on
      the API process avoids a second Chromium herd.

Safety net:
    * Each watch tick is wrapped in try/except — one broken watch
      can't kill the loop.
    * The orchestrator hits its own Redis cache on the hot path so
      back-to-back watches over the same query cost ~ms.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from pricepulse.api.cache import get_rate_limiter, get_search_cache
from pricepulse.config import get_settings
from pricepulse.orchestrator.search import SearchOrchestrator
from pricepulse.storage.db import get_engine
from pricepulse.storage.models import PriceAlert, PriceWatch

log = structlog.get_logger(__name__)

# Hard cap on watches processed per tick. With BROWSER_HEADLESS=false +
# stealth bursts every check is several seconds, so 5/tick × 30s tick
# = ~10 watches/minute upper bound. Plenty for a demo of 1-3 watches.
WATCHER_PARALLEL = 5


async def _due_watches(session_factory: async_sessionmaker[Any], now: datetime) -> list[PriceWatch]:
    """Return active watches whose next check is overdue, oldest first.

    A watch's next check is `last_check_at + interval_min`. NULL
    last_check_at (brand-new watch) is always due."""
    async with session_factory() as db:
        stmt = (
            select(PriceWatch)
            .where(PriceWatch.active.is_(True))
            .order_by(PriceWatch.last_check_at.asc().nulls_first())
        )
        rows = (await db.scalars(stmt)).all()
    due: list[PriceWatch] = []
    for r in rows:
        if r.last_check_at is None:
            due.append(r)
            continue
        last = r.last_check_at if r.last_check_at.tzinfo else r.last_check_at.replace(tzinfo=UTC)
        if now - last >= timedelta(minutes=r.interval_min):
            due.append(r)
    return due[:WATCHER_PARALLEL]


def _pick_best_offer(groups: list[Any]) -> tuple[Decimal, dict] | None:
    """Lowest-price offer across all groups, with a tiny snapshot dict
    suitable for storing in PriceAlert.top_offers (one entry)."""
    best: tuple[Decimal, dict] | None = None
    for g in groups:
        for o in getattr(g, "offers", []) or []:
            try:
                p = Decimal(str(o.price))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if p <= 0:
                continue
            snap = {
                "source": getattr(o, "source", None) or "",
                "price": str(p),
                "name": getattr(o, "name", "") or "",
                "url": getattr(o, "url", "") or "",
                "seller": getattr(o, "seller", None),
            }
            if best is None or p < best[0]:
                best = (p, snap)
    return best


def _top_three(groups: list[Any]) -> list[dict]:
    all_offers: list[tuple[Decimal, dict]] = []
    for g in groups:
        for o in getattr(g, "offers", []) or []:
            try:
                p = Decimal(str(o.price))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if p <= 0:
                continue
            all_offers.append((p, {
                "source": getattr(o, "source", "") or "",
                "price": str(p),
                "name": getattr(o, "name", "") or "",
                "url": getattr(o, "url", "") or "",
            }))
    all_offers.sort(key=lambda x: x[0])
    return [d for _, d in all_offers[:3]]


async def _process_one(watch: PriceWatch, session_factory: async_sessionmaker[Any]) -> None:
    log.info("watcher.check_start", id=watch.id, query=watch.query)
    cache = await get_search_cache()
    limiter = await get_rate_limiter()
    orch = SearchOrchestrator(cache=cache, limiter=limiter)
    now = datetime.now(UTC)
    try:
        _, groups, _, _ = await orch.run(
            watch.query, max_per_source=5, region_id=watch.region_id,
        )
    except Exception as exc:
        log.warning("watcher.search_failed", id=watch.id, error=str(exc))
        async with session_factory() as db:
            row = await db.get(PriceWatch, watch.id)
            if row is None:
                return
            row.last_check_at = now
            row.last_error = str(exc)[:500]
            await db.commit()
        return

    best = _pick_best_offer(groups)
    if best is None:
        # No offers found — record the check but don't error-out the watch.
        async with session_factory() as db:
            row = await db.get(PriceWatch, watch.id)
            if row is None:
                return
            row.last_check_at = now
            row.last_error = "no offers found"
            await db.commit()
        return

    new_price, snap = best
    async with session_factory() as db:
        row = await db.get(PriceWatch, watch.id)
        if row is None:
            return
        prev = row.last_best_price
        # Record latest baseline + clear error.
        row.last_best_price = new_price
        row.last_best_source = snap["source"]
        row.last_best_url = snap["url"]
        row.last_best_name = snap["name"]
        row.last_check_at = now
        row.last_error = None

        # First check ever — establish baseline, skip alert.
        if prev is None or prev <= 0:
            await db.commit()
            log.info("watcher.baseline_set", id=watch.id, price=str(new_price))
            return

        diff_pct = float((new_price - prev) / prev * 100)
        if abs(diff_pct) >= row.threshold_pct:
            alert = PriceAlert(
                watch_id=row.id, owner_key=row.owner_key, query=row.query,
                prev_price=prev, new_price=new_price, diff_pct=diff_pct,
                offer_source=snap["source"], offer_url=snap["url"],
                offer_name=snap["name"], top_offers=_top_three(groups),
            )
            db.add(alert)
            log.info(
                "watcher.alert_created", id=watch.id, diff_pct=round(diff_pct, 2),
                prev=str(prev), new=str(new_price),
            )
        await db.commit()


async def _tick(session_factory: async_sessionmaker[Any]) -> None:
    now = datetime.now(UTC)
    due = await _due_watches(session_factory, now)
    if not due:
        return
    log.debug("watcher.tick", due=len(due))
    # Serial — one stealth-browser-heavy search at a time.
    for w in due:
        try:
            await _process_one(w, session_factory)
        except Exception as exc:
            log.warning("watcher.tick_failed", id=w.id, error=str(exc))


async def run_forever() -> None:
    """Top-level loop. Runs until the asyncio task is cancelled (on
    app shutdown). Always sleeps even after an exception."""
    settings = get_settings()
    tick_sec = max(int(settings.watcher_tick_sec or 30), 5)
    log.info("watcher.start", tick_sec=tick_sec)
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    # First tick is delayed so app boot doesn't race with browser pre-warm.
    await asyncio.sleep(tick_sec)
    while True:
        try:
            await _tick(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("watcher.tick_unhandled", error=str(exc))
        try:
            await asyncio.sleep(tick_sec)
        except asyncio.CancelledError:
            raise


