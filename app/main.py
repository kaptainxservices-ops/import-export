"""FastAPI application assembly. Routing and wiring only — no business logic here."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import deals, health, ingest, matches, review, suppliers
from app.config import get_settings
from app.logging_config import configure_logging

log = logging.getLogger(__name__)


def _allowed_origins() -> list[str]:
    """Where the dashboard is served from.

    Kept explicit rather than '*': these endpoints return a client's board, and a
    wildcard would let any page a user has open read it with their session.
    """
    settings = get_settings()
    origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    if settings.dashboard_origin:
        origins.extend(o.strip() for o in settings.dashboard_origin.split(",") if o.strip())
    return origins


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    log.info("import-export backend v%s starting (env=%s)", __version__, settings.app_env)

    # Fail loudly in logs rather than silently accepting or rejecting everything.
    if not settings.ingest_token:
        log.warning("INGEST_TOKEN is not set — /ingest will refuse every request")
    if settings.is_production and not settings.supabase_url:
        log.warning("SUPABASE_URL is not set in production")

    yield

    log.info("import-export backend shutting down")


app = FastAPI(
    title="import-export backend",
    description="Email extraction, normalisation and matching for iPhone trading.",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(ingest.router)
app.include_router(matches.router)
app.include_router(deals.router)
app.include_router(review.router)
app.include_router(suppliers.router)

# The dashboard runs on a different origin — localhost in development, Vercel in
# production — so the browser will not call this without CORS. Restricted to the
# origins we actually serve rather than '*', because these endpoints read a board.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Ingest-Token"],
)
