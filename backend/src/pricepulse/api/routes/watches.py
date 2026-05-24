"""GET/POST/DELETE /api/v1/watches — price-watch subscriptions
and the matching unread-alert feed.

Auth is intentionally light: anyone can create a watch by sending
`X-Anon-Id: <opaque>` (the frontend generates a UUID on first load and
keeps it in localStorage, same flow as the chat widget). Authenticated
users can swap in `Authorization: Bearer …` and we key everything off
`user-{uuid}` so a single account works across devices.

A background loop (`watcher/loop.py`) ticks every WATCHER_TICK_SEC and
re-runs each due watch through the orchestrator, then writes a
PriceAlert row whenever the best-offer price moved more than the
threshold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from pricepulse.auth.users import fastapi_users
from pricepulse.storage.db import SessionDep
from pricepulse.storage.models import PriceAlert, PriceWatch, User

router = APIRouter(prefix="/watches", tags=["watches"])

_optional_user = fastapi_users.current_user(optional=True)


# ───────────────────────── schemas ─────────────────────────


class WatchCreate(BaseModel):
    query: str = Field(..., min_length=2, max_length=512)
    interval_min: int = Field(default=15, ge=1, le=24 * 60)
    threshold_pct: float = Field(default=2.0, ge=0.1, le=90.0)
    region_id: int = Field(default=213)


class WatchRead(BaseModel):
    id: int
    query: str
    interval_min: int
    threshold_pct: float
    region_id: int
    active: bool
    last_best_price: str | None
    last_best_source: str | None
    last_best_url: str | None
    last_best_name: str | None
    last_check_at: datetime | None
    last_error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_row(cls, row: PriceWatch) -> WatchRead:
        return cls(
            id=row.id, query=row.query, interval_min=row.interval_min,
            threshold_pct=row.threshold_pct, region_id=row.region_id,
            active=row.active,
            last_best_price=str(row.last_best_price) if row.last_best_price is not None else None,
            last_best_source=row.last_best_source,
            last_best_url=row.last_best_url,
            last_best_name=row.last_best_name,
            last_check_at=row.last_check_at, last_error=row.last_error,
            created_at=row.created_at,
        )


class AlertRead(BaseModel):
    id: int
    watch_id: int
    query: str
    prev_price: str
    new_price: str
    diff_pct: float
    offer_source: str | None
    offer_url: str | None
    offer_name: str | None
    top_offers: list[dict] | None
    created_at: datetime
    read_at: datetime | None

    @classmethod
    def from_row(cls, row: PriceAlert) -> AlertRead:
        return cls(
            id=row.id, watch_id=row.watch_id, query=row.query,
            prev_price=str(row.prev_price), new_price=str(row.new_price),
            diff_pct=row.diff_pct,
            offer_source=row.offer_source, offer_url=row.offer_url,
            offer_name=row.offer_name, top_offers=row.top_offers,
            created_at=row.created_at, read_at=row.read_at,
        )


# ───────────────────────── helpers ─────────────────────────


def _owner_key(user: User | None, anon: str | None) -> str:
    """Pick an owner identifier. Authenticated users always win, so a
    user that previously had anon watches *won't* see them after login
    — acceptable for v1; we can migrate on first login later."""
    if user is not None:
        return f"user-{user.id}"
    if anon and 8 <= len(anon) <= 128:
        return f"anon-{anon}"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide either Authorization Bearer JWT or X-Anon-Id header",
    )


# ───────────────────────── endpoints ─────────────────────────


@router.get("", response_model=list[WatchRead])
async def list_watches(
    db: SessionDep,
    user: Annotated[User | None, Depends(_optional_user)] = None,
    x_anon_id: Annotated[str | None, Header(alias="X-Anon-Id")] = None,
) -> list[WatchRead]:
    owner = _owner_key(user, x_anon_id)
    rows = await db.scalars(
        select(PriceWatch).where(PriceWatch.owner_key == owner)
        .order_by(PriceWatch.created_at.desc()),
    )
    return [WatchRead.from_row(r) for r in rows.all()]


@router.post("", response_model=WatchRead, status_code=status.HTTP_201_CREATED)
async def create_watch(
    payload: WatchCreate,
    db: SessionDep,
    user: Annotated[User | None, Depends(_optional_user)] = None,
    x_anon_id: Annotated[str | None, Header(alias="X-Anon-Id")] = None,
) -> WatchRead:
    owner = _owner_key(user, x_anon_id)
    # Dedup by (owner, normalized-ish query). For demo we just lowercase + strip.
    normalized = payload.query.strip().lower()
    existing = await db.scalar(
        select(PriceWatch).where(
            PriceWatch.owner_key == owner,
            func.lower(PriceWatch.query) == normalized,
            PriceWatch.active.is_(True),
        ),
    )
    if existing is not None:
        return WatchRead.from_row(existing)

    row = PriceWatch(
        owner_key=owner, query=payload.query.strip(),
        interval_min=payload.interval_min,
        threshold_pct=payload.threshold_pct, region_id=payload.region_id,
        active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return WatchRead.from_row(row)


@router.get("/alerts", response_model=list[AlertRead])
async def list_alerts(
    db: SessionDep,
    user: Annotated[User | None, Depends(_optional_user)] = None,
    x_anon_id: Annotated[str | None, Header(alias="X-Anon-Id")] = None,
    unread: bool = False,
    limit: int = 50,
) -> list[AlertRead]:
    owner = _owner_key(user, x_anon_id)
    stmt = select(PriceAlert).where(PriceAlert.owner_key == owner)
    if unread:
        stmt = stmt.where(PriceAlert.read_at.is_(None))
    stmt = stmt.order_by(PriceAlert.created_at.desc()).limit(min(max(limit, 1), 200))
    rows = await db.scalars(stmt)
    return [AlertRead.from_row(r) for r in rows.all()]


class _CountResponse(BaseModel):
    unread: int


@router.get("/alerts/count", response_model=_CountResponse)
async def unread_count(
    db: SessionDep,
    user: Annotated[User | None, Depends(_optional_user)] = None,
    x_anon_id: Annotated[str | None, Header(alias="X-Anon-Id")] = None,
) -> _CountResponse:
    owner = _owner_key(user, x_anon_id)
    n = await db.scalar(
        select(func.count(PriceAlert.id)).where(
            PriceAlert.owner_key == owner, PriceAlert.read_at.is_(None),
        ),
    ) or 0
    return _CountResponse(unread=int(n))


@router.post("/alerts/{alert_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    alert_id: int, db: SessionDep,
    user: Annotated[User | None, Depends(_optional_user)] = None,
    x_anon_id: Annotated[str | None, Header(alias="X-Anon-Id")] = None,
) -> None:
    """Idempotent. Re-marking an already-read or non-existent alert is
    a no-op — the UI shouldn't surface a 404 just because the user
    happened to click twice or the alert was deleted from another tab."""
    owner = _owner_key(user, x_anon_id)
    await db.execute(
        update(PriceAlert)
        .where(PriceAlert.id == alert_id, PriceAlert.owner_key == owner,
               PriceAlert.read_at.is_(None))
        .values(read_at=datetime.now(UTC)),
    )
    await db.commit()


# DELETE /{watch_id} is declared LAST so concrete /alerts/* paths above
# are matched first by Starlette. (Otherwise GET /watches/alerts could
# never be reached because FastAPI checks paths in declaration order
# and /alerts would also accept the path param.)
@router.delete("/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watch(
    watch_id: int, db: SessionDep,
    user: Annotated[User | None, Depends(_optional_user)] = None,
    x_anon_id: Annotated[str | None, Header(alias="X-Anon-Id")] = None,
) -> None:
    """Idempotent. Returning 204 even when the watch was already
    deleted (or never existed) keeps the UI simple: clicking «Снять
    слежение» after the watch was wiped from another tab shouldn't
    surface a confusing «not found»."""
    owner = _owner_key(user, x_anon_id)
    await db.execute(
        delete(PriceWatch).where(
            PriceWatch.id == watch_id, PriceWatch.owner_key == owner,
        ),
    )
    await db.commit()


@router.post("/alerts/read-all", response_model=_CountResponse)
async def mark_all_read(
    db: SessionDep,
    user: Annotated[User | None, Depends(_optional_user)] = None,
    x_anon_id: Annotated[str | None, Header(alias="X-Anon-Id")] = None,
) -> _CountResponse:
    owner = _owner_key(user, x_anon_id)
    await db.execute(
        update(PriceAlert)
        .where(PriceAlert.owner_key == owner, PriceAlert.read_at.is_(None))
        .values(read_at=datetime.now(UTC)),
    )
    await db.commit()
    return _CountResponse(unread=0)


# Re-export Decimal so the watcher loop type-hints stay co-located.
_ = Decimal
