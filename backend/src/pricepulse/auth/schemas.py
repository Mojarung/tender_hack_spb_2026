"""Pydantic schemas for fastapi-users + Favorites.

We override `email: EmailStr` with a permissive regex so reserved /
special-use TLDs (.local, .test, .example) work for hackathon demos.
fastapi-users still enforces uniqueness at the DB layer.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi_users import schemas
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from pricepulse.domain.enums import SourceKind

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _check_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("value is not a valid email address")
    return value


class UserRead(schemas.BaseUser[uuid.UUID]):
    email: str
    display_name: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return _check_email(v)


class UserCreate(schemas.BaseUserCreate):
    email: str
    display_name: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return _check_email(v)


class UserUpdate(schemas.BaseUserUpdate):
    email: str | None = None
    display_name: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str | None) -> str | None:
        return _check_email(v) if v else v


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
