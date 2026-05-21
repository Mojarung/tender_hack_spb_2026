"""Pydantic schemas for fastapi-users + Favorites."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi_users import schemas
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from pricepulse.domain.enums import SourceKind


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: str | None = None


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None


# ───────────────────── Favorites ─────────────────────


class FavoriteCreate(BaseModel):
    source: SourceKind
    item_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=1024)
    price: Decimal
    currency: str = "RUB"
    url: HttpUrl
    image: HttpUrl | None = None


class FavoriteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: SourceKind
    item_id: str
    name: str
    price: Decimal
    currency: str
    url: HttpUrl
    image: HttpUrl | None = None
    added_at: datetime
