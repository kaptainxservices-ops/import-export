"""Who is calling, and which client's data they may touch.

**The tenant is never taken from the request.** The browser sends only a Supabase
session token; this resolves the user from it and then looks their tenant up in
`profiles`. A tenant id in a query string or a JSON body is a request to read someone
else's board, and the fact that the dashboard would never send one is not a defence.

Kept in one module rather than in whichever endpoint needed it first, because a second
copy of an authentication check is a second place for it to be subtly weaker.
"""

from __future__ import annotations

import logging

from fastapi import Header, HTTPException, status

from app.db.repository import Repository
from app.dependencies import get_repository

log = logging.getLogger(__name__)


def current_tenant(authorization: str = Header(default="")) -> tuple[str, Repository]:
    """Resolve the caller's tenant from their Supabase session token.

    The repository is fetched *after* the token check rather than injected. An
    unauthenticated request should not cause a database client to be constructed — that
    turns every anonymous probe into work, and made a missing query parameter fail with
    a connection error instead of a validation message.
    """
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")

    repository = get_repository()

    try:
        from app.db.supabase_repo import get_client

        user = get_client().auth.get_user(token)
        user_id = user.user.id if user and user.user else None
    except Exception as error:
        log.warning("token verification failed: %s", error)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session") from None

    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session")

    tenant_id = repository.tenant_for_user(user_id)
    if not tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant for this user")

    return tenant_id, repository
