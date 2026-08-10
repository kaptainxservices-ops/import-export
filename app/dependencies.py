"""Wiring: which Repository the API uses.

Kept in one place and overridable, so tests can point the API at the in-memory store
without touching Supabase, a network, or a credential.
"""

from __future__ import annotations

from app.db.repository import Repository

_override: Repository | None = None


def set_repository(repository: Repository | None) -> None:
    """Point the API at a specific Repository. Used by tests, and by nothing else."""
    global _override
    _override = repository


def get_repository() -> Repository:
    if _override is not None:
        return _override

    from app.db.supabase_repo import SupabaseRepository

    return SupabaseRepository()
