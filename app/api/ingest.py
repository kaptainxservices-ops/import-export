"""The endpoint n8n calls for every message.

Guarded by a shared secret. This URL is public on the internet, and an unguarded
version would let anyone inject fake offers into the client's board.
"""

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, status

from app.config import get_settings
from app.schemas.email import InboundEmail, IngestAccepted

router = APIRouter(tags=["ingest"])
log = logging.getLogger(__name__)


def _authorise(token: str | None) -> None:
    expected = get_settings().ingest_token

    if not expected:
        # Refusing is the safe failure. Accepting everything because the server was
        # misconfigured is how an open endpoint ships to production unnoticed.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INGEST_TOKEN is not configured on the server",
        )

    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Ingest-Token",
        )


@router.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestAccepted,
    summary="Accept one email from n8n",
)
def ingest(
    payload: InboundEmail,
    x_ingest_token: str | None = Header(default=None, alias="X-Ingest-Token"),
) -> IngestAccepted:
    _authorise(x_ingest_token)

    log.info(
        "ingest accepted tenant=%s message_id=%s from=%s attachments=%d body_chars=%d",
        payload.tenant_id,
        payload.message_id,
        payload.from_email,
        len(payload.attachments),
        len(payload.body_text),
    )

    # Phase 2 replaces this: classify -> extract -> normalise -> reconcile -> persist.
    return IngestAccepted(
        message_id=payload.message_id,
        accepted=True,
        detail="received; extraction not implemented yet (Phase 2)",
    )
