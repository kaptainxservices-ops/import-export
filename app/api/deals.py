"""Deals — the wrapper that turns a match into work in progress.

Without this the system finds the deal and then the trader leaves it to execute, which
means the numbers he agreed live in his head and his sent folder rather than anywhere the
team can see. The specification lists deals as day-one, not a later refinement, and the
reason is that a broker's real state is 'three conversations open' rather than 'here is
some supply'.

Two things this module is careful about.

**A deal never relocates a line item.** Adding a lot to a deal does not remove it from the
Sellers list; it marks it in play. The same lot may belong to two deals, and it stays a
price-history point either way.

**The over-allocation check runs here, not only in the browser.** Promising 121 units of a
120-unit lot has to be refused by the thing that writes, because a check that lives only
in the UI is a check that a second tab defeats.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from app.api.deps import current_tenant
from app.brain.deals import Commitment, check_allocation, stale_after_days

router = APIRouter(prefix="/deals", tags=["deals"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------- shapes


class AllocationOut(BaseModel):
    id: str
    side: str
    counterparty_id: str
    counterparty_name: str | None
    offer_id: str | None
    description: str
    quantity: int
    unit_price: float | None
    currency: str | None
    value: float | None
    note: str | None


class DealOut(BaseModel):
    id: str
    reference: int
    title: str | None
    status: str
    status_updated_at: str | None
    keep_offers_visible: bool
    opened_at: str | None
    closed_at: str | None
    outcome: str | None
    quoted_unit_price: float | None
    loss_reason: str | None
    buying: list[AllocationOut]
    selling: list[AllocationOut]
    margin: float | None
    currency: str | None
    untouched_days: int | None = Field(
        default=None,
        description="Days since anyone updated the status, once past the nudge "
        "threshold. A staleness prompt, not a pipeline stage.",
    )


class DealsOut(BaseModel):
    items: list[DealOut]
    open_count: int
    committed_value: float | None
    currency: str | None


class NewDeal(BaseModel):
    title: str | None = None
    status: str = ""


class DealPatch(BaseModel):
    title: str | None = None
    status: str | None = None
    keep_offers_visible: bool | None = None

    def changes(self) -> dict:
        out = {f: getattr(self, f) for f in self.model_fields_set}
        # Touching the status is what the eight-day nudge measures. Set here rather than
        # by a database trigger so that editing a title does not reset the clock — the
        # nudge is about whether anyone has said anything, not whether a row was written.
        if "status" in out:
            out["status_updated_at"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        return out


class CloseDeal(BaseModel):
    outcome: str
    quoted_unit_price: float | None = None
    loss_reason: str | None = None

    @model_validator(mode="after")
    def _sane(self):
        if self.outcome not in ("won", "lost"):
            raise ValueError("outcome must be 'won' or 'lost'")
        # Not enforced as a requirement. A trader closing out at speed should not be
        # blocked by a text box, and a lost deal with no reason is still worth recording.
        return self


class NewAllocation(BaseModel):
    side: str
    counterparty_id: str
    offer_id: str | None = None
    quantity: int
    unit_price: float | None = None
    currency: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _sane(self):
        if self.side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if self.quantity <= 0:
            raise ValueError("quantity must be a positive number")
        return self


class AllocationAdded(BaseModel):
    allocation: AllocationOut
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- endpoints


@router.get("", response_model=DealsOut, summary="Deals in progress")
def list_deals(
    include_closed: bool = Query(False),
    context=Depends(current_tenant),
) -> DealsOut:
    tenant_id, repository = context
    deals = repository.list_deals(tenant_id, include_closed)

    now = datetime.now(timezone.utc)  # noqa: UP017
    items = [_out(deal, now) for deal in deals]

    # Only what is bought on open deals. Summing both sides would double-count the same
    # trade, and summing margin would present an expectation as a fact.
    committed = sum(
        a.value or 0 for deal in deals if deal.is_open for a in deal.buying
    )

    return DealsOut(
        items=items,
        open_count=sum(1 for deal in deals if deal.is_open),
        committed_value=round(committed, 2) if committed else None,
        currency=next(
            (a.currency for deal in deals for a in deal.allocations if a.currency), None
        ),
    )


@router.post("", response_model=DealOut, status_code=status.HTTP_201_CREATED)
def create_deal(payload: NewDeal, context=Depends(current_tenant)) -> DealOut:
    tenant_id, repository = context
    deal = repository.create_deal(tenant_id, payload.title, payload.status)
    return _out(deal, datetime.now(timezone.utc))  # noqa: UP017


@router.patch("/{deal_id}", response_model=DealOut)
def update_deal(deal_id: str, payload: DealPatch, context=Depends(current_tenant)) -> DealOut:
    tenant_id, repository = context

    changes = payload.changes()
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nothing to change")

    if not repository.update_deal(tenant_id, deal_id, changes):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such deal on this board")

    return _require(repository, tenant_id, deal_id)


@router.post("/{deal_id}/close", response_model=DealOut)
def close_deal(deal_id: str, payload: CloseDeal, context=Depends(current_tenant)) -> DealOut:
    """Close a deal out, won or lost.

    The quoted price and the reason are captured here because a lost deal is only worth
    having recorded if it says what we asked and why it went away.
    """
    tenant_id, repository = context

    ok = repository.update_deal(
        tenant_id,
        deal_id,
        {
            "closed_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "outcome": payload.outcome,
            "quoted_unit_price": payload.quoted_unit_price,
            "loss_reason": payload.loss_reason,
        },
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such deal on this board")

    log.info("deal %s closed as %s on tenant %s", deal_id, payload.outcome, tenant_id)
    return _require(repository, tenant_id, deal_id)


@router.post("/{deal_id}/allocations", response_model=AllocationAdded)
def add_allocation(
    deal_id: str, payload: NewAllocation, context=Depends(current_tenant)
) -> AllocationAdded:
    """Commit a quantity of one lot to this deal.

    The check runs before the write and refuses rather than truncating. Silently writing
    120 when 121 was asked for would leave the trader believing he had promised 121.
    """
    tenant_id, repository = context

    if repository.get_deal(tenant_id, deal_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such deal on this board")

    warnings: list[str] = []

    if payload.offer_id:
        lot = repository.get_board_offer(tenant_id, payload.offer_id)
        if lot is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such lot on this board")

        verdict = check_allocation(
            wanted=payload.quantity,
            lot_quantity=lot.quantity,
            existing=[
                Commitment(deal_id=d, deal_reference=r, quantity=q, is_open=o)
                for d, r, q, o in repository.commitments_on_offer(tenant_id, payload.offer_id)
            ],
            deal_id=deal_id,
        )
        if not verdict.allowed:
            raise HTTPException(status.HTTP_409_CONFLICT, verdict.reason)
        warnings = verdict.warnings

    allocation = repository.add_allocation(
        tenant_id,
        deal_id,
        {
            "side": payload.side,
            "counterparty_id": payload.counterparty_id,
            "offer_id": payload.offer_id,
            "quantity": payload.quantity,
            "unit_price": payload.unit_price,
            "currency": payload.currency,
            "note": payload.note,
        },
    )

    return AllocationAdded(allocation=_leg(allocation), warnings=warnings)


@router.delete("/{deal_id}/allocations/{allocation_id}", response_model=DealOut)
def remove_allocation(
    deal_id: str, allocation_id: str, context=Depends(current_tenant)
) -> DealOut:
    tenant_id, repository = context

    if not repository.remove_allocation(tenant_id, allocation_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such allocation on this board")

    return _require(repository, tenant_id, deal_id)


# ---------------------------------------------------------------- plumbing


def _require(repository, tenant_id: str, deal_id: str) -> DealOut:
    deal = repository.get_deal(tenant_id, deal_id)
    if deal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such deal on this board")
    return _out(deal, datetime.now(timezone.utc))  # noqa: UP017


def _leg(a) -> AllocationOut:
    return AllocationOut(
        id=a.id,
        side=a.side,
        counterparty_id=a.counterparty_id,
        counterparty_name=a.counterparty_name,
        offer_id=a.offer_id,
        description=a.description,
        quantity=a.quantity,
        unit_price=a.unit_price,
        currency=a.currency,
        value=a.value,
        note=a.note,
    )


def _out(deal, now: datetime) -> DealOut:
    return DealOut(
        id=deal.id,
        reference=deal.reference,
        title=deal.title,
        status=deal.status,
        status_updated_at=(
            deal.status_updated_at.isoformat() if deal.status_updated_at else None
        ),
        keep_offers_visible=deal.keep_offers_visible,
        opened_at=deal.opened_at.isoformat() if deal.opened_at else None,
        closed_at=deal.closed_at.isoformat() if deal.closed_at else None,
        outcome=deal.outcome,
        quoted_unit_price=deal.quoted_unit_price,
        loss_reason=deal.loss_reason,
        buying=[_leg(a) for a in deal.buying],
        selling=[_leg(a) for a in deal.selling],
        margin=deal.margin,
        currency=next((a.currency for a in deal.allocations if a.currency), None),
        untouched_days=(
            stale_after_days(deal.status_updated_at, now) if deal.is_open else None
        ),
    )
