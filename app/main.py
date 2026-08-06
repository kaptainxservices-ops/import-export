"""FastAPI application assembly. Routing and wiring only — no business logic here."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import health, ingest
from app.config import get_settings
from app.logging_config import configure_logging

log = logging.getLogger(__name__)


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
