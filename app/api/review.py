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

from app.api.deps import current_caller, current_tenant
from app.brain.verdict import judge_import
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



# ---------------------------------------------------------------- list level


class PendingImportOut(BaseModel):
    """One price list, summarised so it can be judged without being opened.

    The counts are the point. A person approving a 487-row list has not read 487 rows
    and never will; they have read "487 parsed, 12 flagged, they usually send 490" and
    decided that looks like a Tuesday.
    """

    id: str
    counterparty_name: str | None
    subject: str | None
    received_at: str | None
    created_at: str | None
    rows_parsed: int
    flagged: int
    parse_warnings: list[str]
    status: str
    previous_row_count: int | None
    verdict: str = Field(description="'healthy', 'check' or 'suspect'")
    verdict_reason: str


class PendingImportsOut(BaseModel):
    items: list[PendingImportOut]
    waiting: int


class ApprovedOut(BaseModel):
    approved: bool
    import_id: str


class RejectedOut(BaseModel):
    rejected: bool
    withdrawn: int = Field(default=0, description="Rows this import added, now withdrawn")
    reopened: int = Field(
        default=0,
        description="Rows this import closed as sold, put back. The half that is easy "
        "to forget: without it a rejected bad parse still destroys the stock it "
        "displaced.",
    )


class FlaggedRowOut(BaseModel):
    id: str
    side: str
    description: str
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    confidence: float | None = None
    review_reason: str | None = None
    source_ref: str | None = None
    source_email_id: str | None = None
    counterparty_name: str | None = None


class FixRowIn(BaseModel):
    """A human's correction to one row.

    Every field optional: a row flagged for a missing price is corrected by supplying a
    price, and making the person retype the description to do it is how a correction
    introduces a second error.
    """

    description: str | None = None
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None

    @model_validator(mode="after")
    def _something_to_change(self):
        if all(
            getattr(self, name) is None
            for name in ("description", "quantity", "unit_price", "currency")
        ):
            raise ValueError("send at least one field to change")
        return self


class FixedOut(BaseModel):
    fixed: bool
    offer_id: str


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


# ---------------------------------------------------------------- list level


def _pending(row: dict) -> PendingImportOut:
    """One import row from the database, as the review screen needs it.

    Pure, so the verdict thresholds are testable without a database. The nested
    `counterparties` and `emails` objects come from PostgREST's join syntax and are
    absent when the foreign key is null — an import whose email was deleted still has
    to render rather than raise.
    """
    counterparty = row.get("counterparties") or {}
    email = row.get("emails") or {}

    rows_parsed = row.get("row_count") or 0
    flagged = row.get("flagged_rows") or 0
    warnings = list(row.get("parse_warnings") or [])
    previous = row.get("previous_row_count")

    verdict = judge_import(
        row_count=rows_parsed,
        flagged_rows=flagged,
        previous_row_count=previous,
        parse_warnings=warnings,
        status=row.get("status") or "applied",
    )

    return PendingImportOut(
        id=row["id"],
        counterparty_name=counterparty.get("name") or counterparty.get("primary_email"),
        subject=email.get("subject"),
        received_at=email.get("received_at"),
        created_at=row.get("created_at"),
        rows_parsed=rows_parsed,
        flagged=flagged,
        parse_warnings=warnings,
        status=row.get("status") or "applied",
        previous_row_count=previous,
        verdict=verdict.level,
        verdict_reason=verdict.reason,
    )


@router.get("/imports", response_model=PendingImportsOut, summary="Price lists awaiting a verdict")
def pending_imports(
    limit: int = Query(50, ge=1, le=200),
    context=Depends(current_tenant),
) -> PendingImportsOut:
    tenant_id, repository = context
    rows = repository.list_pending_imports(tenant_id, limit)
    items = [_pending(row) for row in rows]
    return PendingImportsOut(items=items, waiting=len(items))


@router.post(
    "/imports/{import_id}/approve",
    response_model=ApprovedOut,
    summary="Accept a price list as read",
)
def approve(import_id: str, context=Depends(current_caller)) -> ApprovedOut:
    tenant_id, user_id, repository = context

    if not repository.approve_import(tenant_id, import_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such import on this board")

    log.info("import %s approved on tenant %s by %s", import_id, tenant_id, user_id)
    return ApprovedOut(approved=True, import_id=import_id)


@router.post(
    "/imports/{import_id}/reject",
    response_model=RejectedOut,
    summary="Undo a price list wholesale",
)
def reject(import_id: str, context=Depends(current_caller)) -> RejectedOut:
    """For the case where correcting the rows by hand is not a plan.

    Rejecting does two things, and the second is the one that is easy to forget. The
    rows this import added are withdrawn — obvious. But this import also closed the
    supplier's previous list as sold, and leaving that closed would take their whole
    stock off the board: the bad parse would have destroyed real inventory even after
    being rejected. Both numbers come back so the screen can say so.
    """
    tenant_id, user_id, repository = context

    outcome = repository.reject_import(tenant_id, import_id, user_id)
    if not outcome.get("rejected"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such import on this board")

    return RejectedOut(
        rejected=True,
        withdrawn=outcome.get("withdrawn", 0),
        reopened=outcome.get("reopened", 0),
    )


# ---------------------------------------------------------------- row level


@router.get("/rows", response_model=list[FlaggedRowOut], summary="Rows held back for a person")
def flagged_rows(
    import_id: str | None = Query(default=None),
    context=Depends(current_tenant),
) -> list[FlaggedRowOut]:
    tenant_id, repository = context

    out: list[FlaggedRowOut] = []
    for row in repository.list_flagged_rows(tenant_id, import_id):
        counterparty = row.get("counterparties") or {}
        out.append(
            FlaggedRowOut(
                id=row["id"],
                side=row.get("side") or "sell",
                description=row.get("description") or "",
                quantity=row.get("quantity"),
                unit_price=(
                    float(row["unit_price"]) if row.get("unit_price") is not None else None
                ),
                currency=row.get("currency"),
                confidence=(
                    float(row["confidence"]) if row.get("confidence") is not None else None
                ),
                review_reason=row.get("review_reason"),
                source_ref=row.get("source_ref"),
                source_email_id=row.get("source_email_id"),
                counterparty_name=counterparty.get("name"),
            )
        )
    return out


@router.patch("/rows/{offer_id}", response_model=FixedOut, summary="Correct a row and publish it")
def fix_row(offer_id: str, payload: FixRowIn, context=Depends(current_tenant)) -> FixedOut:
    """Apply a correction and let the row onto the board.

    `needs_review` is cleared by the repository rather than here: a correction that
    leaves the row flagged would be offered for correction again tomorrow, and a person
    who fixes the same row twice stops trusting the queue.
    """
    tenant_id, repository = context

    changes = payload.model_dump(exclude_none=True)
    if not repository.resolve_row(tenant_id, offer_id, changes):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such row on this board")

    log.info("row %s corrected on tenant %s: %s", offer_id, tenant_id, sorted(changes))
    return FixedOut(fixed=True, offer_id=offer_id)
