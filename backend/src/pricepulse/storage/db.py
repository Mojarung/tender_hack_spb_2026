"""Async SQLAlchemy 2.0 engine / session factory.

`database_url` from settings drives the engine. If `SQLITE_URL` is set
(e.g. `sqlite+aiosqlite:///./pricepulse.db`) we use SQLite — handy for
local-dev without spinning Postgres. Otherwise Postgres via asyncpg.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from pricepulse.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure() -> async_sessionmaker[AsyncSession]:
    global _engine, _session_factory
    if _session_factory is None:
        dsn = get_settings().database_url
        # SQLite + AsyncIO has no real pool — skip pool_pre_ping.
        kwargs: dict = {} if dsn.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_async_engine(dsn, **kwargs)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _session_factory


def get_engine() -> AsyncEngine:
    _ensure()
    assert _engine is not None
    return _engine


async def session() -> AsyncIterator[AsyncSession]:
    factory = _ensure()
    async with factory() as s:
        yield s


SessionDep = Annotated[AsyncSession, Depends(session)]
