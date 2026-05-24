import asyncio
import sys

# Принудительно устанавливаем WindowsProactorEventLoopPolicy для корректной работы nodriver на Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        import uvicorn.loops.asyncio
        uvicorn.loops.asyncio.asyncio_setup = lambda *args, **kwargs: None
    except ImportError:
        pass

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator, metrics

from pricepulse.antibot.browser_pool import close_browser_pool
from pricepulse.antibot.google_browser import close_google_browser
from pricepulse.antibot.wb_browser import close_wb_browser
from pricepulse.antibot.yandex_browser import close_yandex_browser
from pricepulse.api.cache import close_rate_limiter, close_search_cache
from pricepulse.api.routes import (
    chat,
    favorites,
    health,
    images,
    price_history,
    runet,
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

    # Pre-warm both stealth browsers SEQUENTIALLY at app boot. Without
    # this, the first concurrent `asyncio.gather(ozon_search, wb_search)`
    # races two `uc.start()` calls in the same process — nodriver
    # internal state gets confused and one launch fails with
    # "Failed to connect to browser". Pre-warming pays the launch cost
    # once at startup (~5-6 s combined) and eliminates the race.
    from pricepulse.antibot.browser_pool import get_browser_pool
    from pricepulse.antibot.google_browser import get_google_browser
    from pricepulse.antibot.wb_browser import get_wb_browser
    from pricepulse.antibot.yandex_browser import get_yandex_browser

    try:
        pool = await get_browser_pool()
        # Force Ozon's Chrome to fully launch by acquiring + releasing a tab.
        async with pool.acquire("warmup"):
            pass
    except Exception as exc:    # never fatal — first request will retry
        import structlog
        structlog.get_logger(__name__).warning("ozon_browser_prewarm_failed", error=repr(exc), exc_info=True)
    try:
        wb = await get_wb_browser()
        await wb._ensure_started()    # type: ignore[attr-defined]
    except Exception as exc:
        import structlog
        structlog.get_logger(__name__).warning("wb_browser_prewarm_failed", error=repr(exc), exc_info=True)
    try:
        yandex = await get_yandex_browser()
        await yandex._ensure_started()    # type: ignore[attr-defined]
    except Exception as exc:
        import structlog
        structlog.get_logger(__name__).warning("yandex_browser_prewarm_failed", error=repr(exc), exc_info=True)
    try:
        google = await get_google_browser()
        await google._ensure_started()    # type: ignore[attr-defined]
    except Exception as exc:
        import structlog
        structlog.get_logger(__name__).warning("google_browser_prewarm_failed", error=repr(exc), exc_info=True)

    yield
    # Tear singletons down cleanly on app exit.
    await close_search_cache()
    await close_rate_limiter()
    await close_browser_pool()
    await close_wb_browser()
    await close_yandex_browser()
    await close_google_browser()


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
    _include_auth_routers(app)
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(stream.router, prefix="/api/v1")
    app.include_router(images.router, prefix="/api/v1")
    app.include_router(price_history.router, prefix="/api/v1")
    app.include_router(sentiment.router, prefix="/api/v1")
    app.include_router(favorites.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(runet.router, prefix="/api/v1")

    _instrument(app)
    return app


app = create_app()
