"""Health check.

Returns 200 only when the API *and* its database are usable. A green health
check that lies about the database is worse than no health check.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app import __version__
from app.config import Settings, get_settings
from app.db.session import ping_database

router = APIRouter(tags=["system"])


class DatabaseHealth(BaseModel):
    status: str
    latency_ms: float | None = None
    reason: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: DatabaseHealth


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and database readiness",
    responses={503: {"model": HealthResponse, "description": "A dependency is unreachable"}},
)
def health(request: Request, response: Response) -> HealthResponse:
    # Read the settings the app was built with, not the cached global — otherwise
    # create_app(settings) is silently ignored and tests assert against prod config.
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    db = ping_database()

    if not db.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if db.ok else "degraded",
        version=__version__,
        environment=settings.env,
        database=DatabaseHealth(
            status="ok" if db.ok else "unreachable",
            latency_ms=db.latency_ms,
            reason=db.reason,
        ),
    )
