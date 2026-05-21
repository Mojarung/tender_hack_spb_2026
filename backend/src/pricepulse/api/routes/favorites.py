"""GET/POST/DELETE /api/v1/favorites — per-user wishlist.

All endpoints require a valid JWT (Bearer in `Authorization`). On the
backend they're scoped to `current_user.id` so two users can't see
each other's lists.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from pricepulse.auth.schemas import FavoriteCreate, FavoriteRead
from pricepulse.auth.users import CurrentUserDep
from pricepulse.storage.db import SessionDep
from pricepulse.storage.models import Favorite

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.get("", response_model=list[FavoriteRead])
async def list_favorites(user: CurrentUserDep, db: SessionDep) -> list[FavoriteRead]:
    rows = await db.scalars(
        select(Favorite).where(Favorite.user_id == user.id).order_by(Favorite.added_at.desc())
    )
    return [FavoriteRead.model_validate(r) for r in rows.all()]


@router.post("", response_model=FavoriteRead, status_code=status.HTTP_201_CREATED)
async def add_favorite(
    payload: FavoriteCreate, user: CurrentUserDep, db: SessionDep,
) -> FavoriteRead:
    existing = await db.scalar(
        select(Favorite).where(
            Favorite.user_id == user.id,
            Favorite.source == payload.source.value,
            Favorite.item_id == payload.item_id,
        )
    )
    if existing is not None:
        return FavoriteRead.model_validate(existing)

    row = Favorite(
        user_id=user.id,
        source=payload.source.value,
        item_id=payload.item_id,
        name=payload.name,
        price=payload.price,
        currency=payload.currency,
        url=str(payload.url),
        image=str(payload.image) if payload.image else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return FavoriteRead.model_validate(row)


@router.delete("/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    favorite_id: int, user: CurrentUserDep, db: SessionDep,
) -> None:
    deleted = await db.execute(
        delete(Favorite).where(
            Favorite.id == favorite_id, Favorite.user_id == user.id
        )
    )
    if deleted.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="favorite not found")
    await db.commit()
