"""fastapi-users wiring — UserManager, JWT, FastAPIUsers instance.

Exports:
    fastapi_users   — FastAPIUsers[User, UUID]
    auth_backend    — JWT bearer (24h lifetime by default)
    current_user    — dependency for protected routes
    current_active_user — dependency that also enforces is_active
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from pricepulse.config import Settings, get_settings
from pricepulse.storage.db import session
from pricepulse.storage.models import User


async def get_user_db(
    db: AsyncSession = Depends(session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(db, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    @property
    def reset_password_token_secret(self) -> str:
        return get_settings().auth_jwt_secret

    @property
    def verification_token_secret(self) -> str:
        return get_settings().auth_jwt_secret


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def _strategy_factory(settings: Settings) -> JWTStrategy:
    return JWTStrategy(
        secret=settings.auth_jwt_secret,
        lifetime_seconds=settings.auth_jwt_lifetime_seconds,
    )


def get_jwt_strategy() -> JWTStrategy:
    return _strategy_factory(get_settings())


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users: FastAPIUsers[User, uuid.UUID] = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)

current_user = fastapi_users.current_user()
current_active_user = fastapi_users.current_user(active=True)
CurrentUserDep = Annotated[User, Depends(current_active_user)]
