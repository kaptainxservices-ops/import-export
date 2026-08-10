"""Liveness and readiness.

Two endpoints, deliberately. `/health` says the process is running and nothing else;
`/health/ready` also checks the database.

Keeping them apart matters at deploy time. Render restarts a service whose health check
fails, so if `/health` depended on Supabase, a thirty-second Supabase blip would take
the backend down too — and it would stay down retrying while emails piled up. Liveness
must only answer for the thing it controls.

Both are unauthenticated: Render's health check and UptimeRobot can't carry a secret.
Neither reveals anything a caller could use.
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.config import get_settings

router = APIRouter(tags=["ops"])
log = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str


class ReadyResponse(BaseModel):
    status: str
    database: str
    detail: str = ""


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", version=__version__, env=settings.app_env)


@router.get("/health/ready", response_model=ReadyResponse, summary="Readiness check")
def ready() -> ReadyResponse:
    """Can this instance actually reach the database?

    Not wired to Render's health check on purpose — this is for you to hit after a
    deploy, and for UptimeRobot, where a failure should page rather than restart.
    """
    try:
        from app.dependencies import get_repository

        repository = get_repository()
        # A tenant that cannot exist: proves the round trip without reading real data.
        repository.get_tenant("00000000-0000-0000-0000-000000000000")
        return ReadyResponse(status="ok", database="reachable")
    except Exception as error:
        log.warning("readiness check failed: %s", error)
        return ReadyResponse(status="degraded", database="unreachable", detail=str(error)[:200])
