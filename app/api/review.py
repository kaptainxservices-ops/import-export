"""The queue of emails the pipeline declined to file, and the two ways to resolve one.

Every stage of the brain is allowed to decline. A sender it cannot establish, a side it
cannot decide — those emails are stored and flagged rather than guessed at, because an
email in a queue costs somebody a click and a supplier's catalogue filed against the
wrong counterparty corrupts two boards at once.

That reasoning appears throughout the codebase and until now the queue it points at did
not exist, so roughly a quarter of every batch went quietly nowhere.

Resolving an email does not merely label it. It re-runs extraction and reconciliation
over the stored message, because attributing a WhatsApp offer to the right supplier is
worth nothing if their stock does not then appear on the board.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from app.api.deps import current_tenant
from app.pipeline import reprocess_email

router = APIRouter(prefix="/review", tags=["review"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------- shapes


class ReviewItemOut(BaseModel):
    id: str
    received_at: str
    from_email: str
    subject: str
    reason: str = Field(description="'sender', 'classification' or 'both'")
    needs_sender_review: bool
    needs_classification: bool
    classification: str
    counterparty_id: str | None
    counterparty_name: str | None
    counterparty_method: str
    counterparty_confidence: float
    suggested_sender_name: str | None
    body_preview: str
    attachment_count: int


class QueueOut(BaseModel):
    items: list[ReviewItemOut]
    total: int
    awaiting_sender: int
    awaiting_classification: int
    emails_seen: int = Field(
        description="Every email on this board, reviewed or not. An empty queue means "
        "the opposite thing depending on whether this is zero."
    )


class ResolveIn(BaseModel):
    """What a person decided.

    Either field may be sent alone: an email can need a sender, a side, or both, and
    forcing an answer to a question that was never in doubt invites a wrong one.
    """

    email_id: str
    counterparty_email: str | None = Field(
        default=None, description="Attribute to this supplier, creating them if new"
    )
    counterparty_name: str | None = None
    side: str | None = Field(default=None, description="'sell' or 'buy'")

    @model_validator(mode="after")
    def _something_to_do(self):
        if not self.counterparty_email and not self.side:
            raise ValueError("send a counterparty_email, a side, or both")
        if self.side is not None and self.side not in ("sell", "buy"):
            raise ValueError("side must be 'sell' or 'buy'")
        return self


class ResolveOut(BaseModel):
    email_id: str
    outcome: str
    counterparty_id: str | None = None
    side: str | None = None
    row_count: int = 0
    inserted: int = 0
    updated: int = 0
    closed: int = 0
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- endpoints


@router.get("/queue", response_model=QueueOut, summary="Emails awaiting a human")
def queue(
    limit: int = Query(200, ge=1, le=500),
    context=Depends(current_tenant),
) -> QueueOut:
    tenant_id, repository = context
    items = repository.list_review_queue(tenant_id, limit)

    return QueueOut(
        items=[
            ReviewItemOut(
                id=item.id,
                received_at=item.received_at.isoformat(),
                from_email=item.from_email,
                subject=item.subject,
                reason=item.reason,
                needs_sender_review=item.needs_sender_review,
                needs_classification=item.needs_classification,
                classification=item.classification,
                counterparty_id=item.counterparty_id,
                counterparty_name=item.counterparty_name,
                counterparty_method=item.counterparty_method,
                counterparty_confidence=item.counterparty_confidence,
                suggested_sender_name=item.suggested_sender_name,
                body_preview=item.body_preview,
                attachment_count=item.attachment_count,
            )
            for item in items
        ],
        total=len(items),
        awaiting_sender=sum(1 for i in items if i.needs_sender_review),
        awaiting_classification=sum(1 for i in items if i.needs_classification),
        emails_seen=repository.count_emails(tenant_id),
    )


@router.post("/resolve", response_model=ResolveOut, summary="Attribute an email and re-run it")
def resolve(payload: ResolveIn, context=Depends(current_tenant)) -> ResolveOut:
    tenant_id, repository = context

    # Read before write. This is what proves the email belongs to the caller's tenant,
    # and it has to happen before a counterparty is created — otherwise a probe with a
    # stranger's email id leaves a real supplier record behind on this board.
    stored = repository.get_stored_email(tenant_id, payload.email_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such email on this board")

    counterparty_id = None
    if payload.counterparty_email:
        email = payload.counterparty_email.strip().lower()
        counterparty = repository.find_counterparty(tenant_id, email)
        if counterparty is None:
            counterparty = repository.create_counterparty(
                tenant_id, email, payload.counterparty_name
            )
        counterparty_id = counterparty.id

    if not repository.attribute_email(tenant_id, payload.email_id, counterparty_id, payload.side):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such email on this board")

    result = reprocess_email(tenant_id, payload.email_id, repository)

    log.info(
        "review resolved tenant=%s email=%s outcome=%s rows=%d",
        tenant_id, payload.email_id, result.outcome, result.row_count,
    )

    reconciled = result.reconcile
    return ResolveOut(
        email_id=payload.email_id,
        outcome=result.outcome,
        counterparty_id=result.counterparty_id or counterparty_id,
        side=result.side,
        row_count=result.row_count,
        inserted=reconciled.inserted if reconciled else 0,
        updated=reconciled.updated if reconciled else 0,
        closed=reconciled.closed if reconciled else 0,
        notes=result.notes,
    )
