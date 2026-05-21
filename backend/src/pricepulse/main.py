from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator, metrics

from pricepulse.api.routes import admin, health, search, stream
from pricepulse.config import get_settings
from pricepulse.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    # Place to wire Redis pool, DB engine, browser pool warmup,
    # sentiment model warmup, etc.
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
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(stream.router, prefix="/api/v1")

    _instrument(app)
    return app


app = create_app()
