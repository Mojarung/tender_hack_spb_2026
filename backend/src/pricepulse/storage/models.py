"""SQLAlchemy ORM models.

User + Favorite models are integrated with fastapi-users (UUID id,
SQLAlchemyBaseUserTableUUID). `Query`/`Offer` stay around for the
arq worker that backfills price-history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    """fastapi-users User table.

    Inherits: id (UUID), email, hashed_password, is_active, is_superuser,
    is_verified. We extend with optional display name + a relationship.
    """

    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    favorites: Mapped[list[Favorite]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "item_id", name="uq_favorite_user_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    # E.g. ("wb", "825188791") or ("ozon", "<sku>") — same shape as ProductOffer.
    source: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(1024))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    url: Mapped[str] = mapped_column(String(2048))
    image: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped[User] = relationship(back_populates="favorites")


class Query(Base):
    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw: Mapped[str] = mapped_column(String(512))
    normalized: Mapped[str] = mapped_column(String(512), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(1024))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    url: Mapped[str] = mapped_column(String(2048))
    image: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PriceWatch(Base):
    """A subscription that re-runs a saved query on a schedule and
    fires a PriceAlert whenever the best-offer price moves more than
    `threshold_pct`. owner_key is a free-form string — for anon users
    it's the localStorage anon-id sent via X-Anon-Id; for signed-in
    users we use `f"user-{user.id}"` so a single account works across
    devices once the user logs in."""

    __tablename__ = "price_watches"
    __table_args__ = (
        Index("ix_pricewatch_owner_active", "owner_key", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_key: Mapped[str] = mapped_column(String(128), index=True)
    query: Mapped[str] = mapped_column(String(512))
    region_id: Mapped[int] = mapped_column(Integer, default=213)
    interval_min: Mapped[int] = mapped_column(Integer, default=15)
    threshold_pct: Mapped[float] = mapped_column(Float, default=2.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Baseline used to compute deltas; updated after every successful check.
    last_best_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    last_best_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_best_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_best_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )

    alerts: Mapped[list[PriceAlert]] = relationship(
        back_populates="watch", cascade="all, delete-orphan", lazy="selectin",
    )


class PriceAlert(Base):
    """One movement event of a watched query — created when the
    best-offer price diverges from the recorded baseline by more than
    the watch's threshold."""

    __tablename__ = "price_alerts"
    __table_args__ = (
        Index("ix_pricealert_owner_read", "owner_key", "read_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    watch_id: Mapped[int] = mapped_column(
        ForeignKey("price_watches.id", ondelete="CASCADE"), index=True,
    )
    # Denormalised for fast unread-count queries without a JOIN.
    owner_key: Mapped[str] = mapped_column(String(128), index=True)
    query: Mapped[str] = mapped_column(String(512))
    prev_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    new_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    diff_pct: Mapped[float] = mapped_column(Float)
    # Snapshot of the best offer at alert time so the UI shows context.
    offer_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    offer_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    offer_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Pretty-printed top-3 across all sources at the moment of the alert.
    top_offers: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    watch: Mapped[PriceWatch] = relationship(back_populates="alerts")


__all__ = ["Base", "Favorite", "Offer", "PriceAlert", "PriceWatch", "Query", "User"]
