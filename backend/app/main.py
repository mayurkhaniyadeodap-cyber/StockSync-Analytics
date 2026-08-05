"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.routes import analytics, auth, health, imports, reports, shopify
from app.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.headers import SecurityHeadersMiddleware
from app.core.logging import configure_logging
from app.db.session import get_session_factory, ping_database
from app.services.reports import reclaim_interrupted as reclaim_interrupted_reports
from app.services.sync import reclaim_interrupted_runs
from app.workers import runner

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    log.info("StockSync Analytics %s starting (env=%s)", __version__, settings.env)
    log.info("database: %s", settings.safe_database_url())

    status = ping_database()
    if status.ok:
        log.info("database reachable (%.1f ms)", status.latency_ms or 0.0)
    else:
        # Not fatal: the API should boot and report the problem through
        # /api/health rather than crash-looping in front of whoever has to fix it.
        log.warning(
            "database unreachable at startup (%s) — /api/health will report 503", status.reason
        )

    # Say which Shopify credential is in play. A store that turns out to be
    # configured by a file nobody remembered editing is a confusing hour.
    if settings.env_shopify_credential_active:
        log.info(
            "shopify: using the .env credential for %s (a stored connection overrides it)",
            settings.shopify_store_url,
        )
    elif settings.has_env_shopify_credential:
        log.warning(
            "shopify: SHOPIFY_ADMIN_API_TOKEN is set but ignored in production — "
            "connect the store through the app so the token is encrypted at rest"
        )

    # Jobs run inline under test so a test never races a worker thread.
    runner.configure(inline=settings.env == "test")

    # Nothing that was mid-sync when this process last stopped is still
    # running, and a row left at 'running' blocks every future sync.
    if status.ok:
        try:
            with get_session_factory()() as db:
                reclaim_interrupted_runs(db)
                reclaim_interrupted_reports(db)
        except SQLAlchemyError:
            # A schema that predates M3 is not a reason to refuse to boot.
            log.warning("could not reclaim interrupted sync runs", exc_info=True)

    yield

    # Let an in-flight sync finish rather than leaving a run stuck at 'running'
    # with no worker behind it.
    runner.shutdown(wait=True)
    log.info("StockSync Analytics shutting down")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    app = FastAPI(
        title="StockSync Analytics API",
        description="Inventory & Shopify sales reconciliation.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )
    app.state.settings = settings

    # Added first, so it runs outermost and its headers reach even the responses
    # CORS rejects. Starlette applies middleware in reverse registration order.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # the JWT rides in an httpOnly cookie from M1
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    )

    register_exception_handlers(app)

    api = APIRouter(prefix=settings.api_prefix)
    api.include_router(health.router)
    api.include_router(analytics.router)
    api.include_router(auth.router)
    api.include_router(imports.router)
    api.include_router(reports.router)
    api.include_router(shopify.router)
    app.include_router(api)

    return app


app = create_app()
