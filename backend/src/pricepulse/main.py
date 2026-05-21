from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pricepulse.api.routes import health, search, stream
from pricepulse.config import get_settings
from pricepulse.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    # Place to wire Redis pool, DB engine, browser pool warmup, etc.
    yield
    # Place to drain pools, close connections.


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
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(stream.router, prefix="/api/v1")
    return app


app = create_app()
