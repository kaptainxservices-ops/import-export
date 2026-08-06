"""Liveness endpoint. Deliberately unauthenticated: Render's health check and
UptimeRobot both hit it, and neither can carry a secret."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.config import get_settings

router = APIRouter(tags=["ops"])


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", version=__version__, env=settings.app_env)
