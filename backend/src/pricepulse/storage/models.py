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
    DateTime,
    ForeignKey,
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

    favorites: Mapped[list["Favorite"]] = relationship(
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

    user: Mapped["User"] = relationship(back_populates="favorites")


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


__all__ = ["Base", "User", "Favorite", "Query", "Offer"]
