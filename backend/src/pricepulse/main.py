from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator, metrics

from pricepulse.api.routes import (
    admin,
    chat,
    favorites,
    health,
    price_history,
    search,
    sentiment,
    stream,
)
from pricepulse.auth.schemas import UserCreate, UserRead, UserUpdate
from pricepulse.auth.users import auth_backend, fastapi_users
from pricepulse.config import get_settings
from pricepulse.core.logging import configure_logging
from pricepulse.storage.db import get_engine
from pricepulse.storage.models import Base


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    # Create tables when running against SQLite (no Alembic in dev path).
    # On Postgres production we rely on alembic — `create_all` is idempotent.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def _instrument(app: FastAPI) -> None:
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health"],
        should_instrument_requests_inprogress=True,
        inprogress_labels=True,
    )
    instrumentator.add(metrics.latency(buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10)))
    instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def _include_auth_routers(app: FastAPI) -> None:
    """Wires the fastapi-users routers under /auth and /users."""
    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/auth/jwt", tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/auth", tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users", tags=["users"],
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PricePulse API",
        version="0.1.0",
        description="Aggregated price search across Russian marketplaces and open Runet.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(admin.router)
    _include_auth_routers(app)
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(stream.router, prefix="/api/v1")
    app.include_router(price_history.router, prefix="/api/v1")
    app.include_router(sentiment.router, prefix="/api/v1")
    app.include_router(favorites.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")

    _instrument(app)
    return app


app = create_app()
