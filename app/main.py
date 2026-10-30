"""FastAPI application assembly. Routing and wiring only — no business logic here."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, board_refresher
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

    # Printed at startup because a CORS rejection is invisible from the server side:
    # the browser refuses the response and the log shows a perfectly ordinary 200. The
    # only way to tell a missing origin from a dead service is to be able to read what
    # the service thinks it allows.
    log.info("CORS origins allowed: %s", ", ".join(_allowed_origins()))
    if settings.dashboard_origin_regex:
        log.info("CORS origin pattern: %s", settings.dashboard_origin_regex)

    # Fail loudly in logs rather than silently accepting or rejecting everything.
    if not settings.ingest_token:
        log.warning("INGEST_TOKEN is not set — /ingest will refuse every request")
    if settings.is_production and not settings.supabase_url:
        log.warning("SUPABASE_URL is not set in production")

    refresher = _start_board_refresher(settings)

    yield

    if refresher is not None:
        refresher.stop()
        board_refresher.set_refresher(None)
    log.info("import-export backend shutting down")


def _start_board_refresher(settings):
    """The stored match board's background worker. Not started without Supabase: tests
    and local runs on the in-memory store have no board table to keep."""
    if not settings.board_refresh_enabled or not settings.supabase_url:
        log.info("board refresher off (enabled=%s, supabase=%s)",
                 settings.board_refresh_enabled, bool(settings.supabase_url))
        return None

    from app.board_store import SupabaseBoardDB
    from app.dependencies import get_repository

    refresher = board_refresher.BoardRefresher(
        lambda: SupabaseBoardDB(get_repository()),
        full_every_hours=settings.board_full_rebuild_hours,
    )
    board_refresher.set_refresher(refresher)
    refresher.start()
    return refresher


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


@app.middleware("http")
async def refresh_board_after_writes(request: Request, call_next):
    """Any request that may have written an offer asks the stored board to catch up.

    Deliberately broad — every successful POST, PATCH, PUT and DELETE — rather than a
    call in each endpoint that writes: the refresh reads what actually changed, so a
    request that changed nothing costs one small query, while an endpoint added later
    without the call would leave the board quietly stale.
    """
    response = await call_next(request)
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and response.status_code < 400:
        board_refresher.request_refresh()
    return response

# The dashboard runs on a different origin — localhost in development, Vercel in
# production — so the browser will not call this without CORS. Restricted to the
# origins we actually serve rather than '*', because these endpoints read a board.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    # Matches preview deployments, whose hostname changes on every push.
    allow_origin_regex=get_settings().dashboard_origin_regex or None,
    allow_credentials=True,
    # PATCH and DELETE are not optional extras here. The dashboard corrects a flagged
    # row with PATCH /review/rows/{id}, edits a supplier with PATCH /suppliers/{id},
    # and removes a deal leg with DELETE. A method missing from this list is refused by
    # the browser at the preflight, before the request is sent — so the endpoint can be
    # perfectly healthy and the button still does nothing but say "Failed to fetch".
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Ingest-Token"],
)
