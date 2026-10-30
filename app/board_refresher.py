"""Keeps the stored match board current, in the background.

One daemon thread per process. It wakes when asked — the app asks after every request
that may have written something — waits a moment for the writes to settle, and runs an
incremental refresh for every tenant. With nothing asking it, it runs a full rebuild
every few hours as a safety net, and once at startup so a deploy that changes the
matching rules reaches every stored row.

The request that triggered it has already been answered by then: an email is not held
open while the board catches up.
"""

from __future__ import annotations

import logging
import threading
import time

from app import board_store

log = logging.getLogger(__name__)


class BoardRefresher:
    def __init__(self, db_factory, *, settle_seconds: float = 2.0, full_every_hours: float = 6.0):
        self._db_factory = db_factory
        self._settle = settle_seconds
        self._full_every = full_every_hours * 3600
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_full = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="board-refresher", daemon=True)
        self._thread.start()
        log.info("board refresher started (full rebuild every %.1fh)", self._full_every / 3600)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request(self) -> None:
        """Something may have changed. Cheap and non-blocking: many calls, one refresh."""
        self._wake.set()

    def _run(self) -> None:
        self._cycle(full=True)
        while not self._stop.is_set():
            remaining = max(1.0, self._full_every - (time.monotonic() - self._last_full))
            woken = self._wake.wait(timeout=remaining)
            if self._stop.is_set():
                return
            if woken:
                # Let a burst of writes (one email is hundreds of rows, and n8n may post
                # several emails back to back) land before reading what changed.
                time.sleep(self._settle)
                self._wake.clear()
                self._cycle(full=False)
            else:
                self._cycle(full=True)

    def _cycle(self, *, full: bool) -> None:
        try:
            db = self._db_factory()
            for tenant_id in db.tenant_ids():
                try:
                    board_store.refresh(db, tenant_id, full=full)
                except Exception as error:  # noqa: BLE001
                    # One tenant's failure must not starve the others, and the next
                    # trigger retries from the same watermark, so nothing is skipped.
                    if _table_missing(error):
                        self._warn_missing()
                        return
                    log.exception("board refresh failed tenant=%s full=%s", tenant_id, full)
        except Exception:  # noqa: BLE001
            log.exception("board refresh cycle failed")
        finally:
            # Set even on failure: a database that is down should be retried by the
            # next trigger or the next interval, not in a tight loop.
            if full:
                self._last_full = time.monotonic()


    _warned = False

    def _warn_missing(self) -> None:
        # Every write would otherwise log the same traceback until the migration is run.
        if not self._warned:
            self._warned = True
            log.warning(
                "board_matches does not exist yet -- run supabase/migrations/"
                "0017_board_matches.sql. The dashboard computes matches live until then."
            )


def _table_missing(error: Exception) -> bool:
    text = str(error)
    return "board_match" in text and (
        "does not exist" in text or "PGRST205" in text or "schema cache" in text
    )


_refresher: BoardRefresher | None = None


def get_refresher() -> BoardRefresher | None:
    return _refresher


def set_refresher(refresher: BoardRefresher | None) -> None:
    global _refresher
    _refresher = refresher


def request_refresh() -> None:
    if _refresher is not None:
        _refresher.request()
