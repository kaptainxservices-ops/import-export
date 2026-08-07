"""Daily list reconciliation: deciding what a new list means for what we already hold.

Suppliers send their complete current stock every time. That single fact is what makes
the board self-maintaining, because it licences one powerful inference: **an item that
was on yesterday's list and is absent from today's has sold.** No message needs to be
sent and nobody has to mark anything by hand.

Four cases arise when a list arrives:

    identical to last time      -> refresh the timestamp, nothing else changes
    same item, new price or qty -> update the row and log the old value
    present before, absent now  -> it sold; close it
    never seen                  -> insert

The third case is the valuable one and also the dangerous one, because it is the only
action here that destroys information. Everything in this module follows from a single
asymmetry:

    **Insertions are harmless. Closures are destructive.**

A wrong new row is noise on a screen; someone ignores it. Wrongly closing a live
120-unit lot removes real stock from the board, and the broker never learns that the
deal was there. So closure is hedged four ways — a list must be believed complete, its
row count must be plausible, it must not be empty, and it must not be a duplicate
import — and when any check is uncertain, nothing closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ActionKind = Literal["refresh", "update", "insert", "close", "reopen"]
ImportStatus = Literal[
    "applied",
    "flagged_low_row_count",
    "flagged_partial_list",
    "failed",
]

# Below half a supplier's previous row count, assume the parse failed rather than that
# they sold two-thirds of their stock overnight.
LOW_ROW_COUNT_RATIO = 0.5

# A price that moves by more than this is more likely a parsing error than a reprice.
# The number-format bugs found in the samples produced exactly this shape: €1,079 read
# as €1.07 is a 1000x move that looks like an extraordinary deal.
IMPLAUSIBLE_PRICE_FACTOR = 5.0


@dataclass(frozen=True)
class ExistingOffer:
    """An offer already on the board."""

    id: str
    identity_key: str
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    status: str = "live"
    last_confirmed_at: datetime | None = None


@dataclass(frozen=True)
class IncomingOffer:
    """A line extracted from the list that just arrived."""

    identity_key: str
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    description: str = ""
    source_ref: str = ""


@dataclass(frozen=True)
class FieldChange:
    field: str
    old: str | None
    new: str | None


@dataclass
class Action:
    kind: ActionKind
    identity_key: str
    offer_id: str | None = None
    incoming: IncomingOffer | None = None
    changes: list[FieldChange] = field(default_factory=list)
    reason: str = ""
    needs_review: bool = False


@dataclass
class ReconcileResult:
    actions: list[Action] = field(default_factory=list)
    status: ImportStatus = "applied"
    notes: list[str] = field(default_factory=list)
    row_count: int = 0
    duplicate_rows: int = 0

    def _count(self, kind: ActionKind) -> int:
        return sum(1 for a in self.actions if a.kind == kind)

    @property
    def inserted(self) -> int:
        return self._count("insert")

    @property
    def updated(self) -> int:
        return self._count("update")

    @property
    def refreshed(self) -> int:
        return self._count("refresh")

    @property
    def closed(self) -> int:
        return self._count("close")

    @property
    def reopened(self) -> int:
        return self._count("reopen")

    @property
    def needing_review(self) -> int:
        return sum(1 for a in self.actions if a.needs_review)


def reconcile(
    existing: list[ExistingOffer],
    incoming: list[IncomingOffer],
    *,
    previous_row_count: int | None = None,
    is_complete_list: bool | None = None,
    low_row_count_ratio: float = LOW_ROW_COUNT_RATIO,
) -> ReconcileResult:
    """Work out what a newly arrived list means.

    Nothing is written here — the caller applies the actions. Keeping the decision
    separate from the writing is what makes this testable, and reconciliation is the
    one place where a silent mistake is expensive.

    `is_complete_list` is deliberately three-valued. True licences closures, False
    forbids them, and **None — undecided — also forbids them**. A supplier who normally
    sends full lists will occasionally fire off "just got 50 more 15PMs", and reading
    that as a complete list would close everything else they stock.
    """
    result = ReconcileResult(row_count=len(incoming))

    deduped, duplicates = _dedupe(incoming)
    result.duplicate_rows = duplicates
    if duplicates:
        result.notes.append(
            f"{duplicates} duplicate identities in one list; kept the first of each"
        )

    if not deduped:
        # An empty parse of a non-empty email is a failure, not an empty stock list.
        # Treating it as "they sold everything" would close the supplier's entire board.
        result.status = "failed"
        result.notes.append("no rows extracted; nothing closed")
        return result

    live = {o.identity_key: o for o in existing if o.status == "live"}
    closed = {o.identity_key: o for o in existing if o.status != "live"}

    may_close, close_note = _closure_permitted(
        len(deduped), previous_row_count, is_complete_list, low_row_count_ratio
    )
    if close_note:
        result.notes.append(close_note)
    if not may_close:
        result.status = (
            "flagged_low_row_count"
            if "row count" in close_note
            else "flagged_partial_list"
        )

    for item in deduped:
        current = live.get(item.identity_key)

        if current is not None:
            result.actions.append(_compare(current, item))
            continue

        previously_closed = closed.get(item.identity_key)
        if previously_closed is not None:
            result.actions.append(
                Action(
                    kind="reopen",
                    identity_key=item.identity_key,
                    offer_id=previously_closed.id,
                    incoming=item,
                    reason="listed again after being closed",
                )
            )
            continue

        result.actions.append(
            Action(kind="insert", identity_key=item.identity_key, incoming=item)
        )

    if may_close:
        seen = {item.identity_key for item in deduped}
        for key, offer in live.items():
            if key not in seen:
                result.actions.append(
                    Action(
                        kind="close",
                        identity_key=key,
                        offer_id=offer.id,
                        reason="absent from a complete list; assumed sold",
                    )
                )

    return result


def _dedupe(incoming: list[IncomingOffer]) -> tuple[list[IncomingOffer], int]:
    """Keep the first row of each identity.

    The same product does appear twice in one list — under two section headings, or
    once with an EAN and once without. Summing the quantities would invent stock; the
    two rows are one lot described twice, not two lots.
    """
    seen: set[str] = set()
    out: list[IncomingOffer] = []
    duplicates = 0

    for item in incoming:
        if item.identity_key in seen:
            duplicates += 1
            continue
        seen.add(item.identity_key)
        out.append(item)

    return out, duplicates


def _closure_permitted(
    row_count: int,
    previous_row_count: int | None,
    is_complete_list: bool | None,
    ratio: float,
) -> tuple[bool, str]:
    """Whether absence may be read as sold. Every uncertain answer is 'no'."""
    if is_complete_list is not True:
        state = "undecided" if is_complete_list is None else "partial"
        return False, f"list is {state}; closed nothing"

    if previous_row_count and row_count < previous_row_count * ratio:
        # The scenario this exists for: a sender's list normally yields 487 rows, the
        # attachment is malformed one morning and 12 parse. Without this check,
        # reconciliation closes 475 live offers and no error is raised anywhere.
        return False, (
            f"row count collapsed from {previous_row_count} to {row_count}; "
            f"closed nothing and flagged for review"
        )

    return True, ""


def _compare(current: ExistingOffer, item: IncomingOffer) -> Action:
    """Same lot as before: unchanged, or repriced."""
    changes: list[FieldChange] = []
    needs_review = False
    reasons: list[str] = []

    if _differs(current.unit_price, item.unit_price):
        changes.append(
            FieldChange("unit_price", _text(current.unit_price), _text(item.unit_price))
        )
        if _implausible_move(current.unit_price, item.unit_price):
            needs_review = True
            reasons.append(
                f"price moved from {current.unit_price} to {item.unit_price}, "
                f"which is more likely a parsing error than a reprice"
            )

    if current.quantity != item.quantity:
        changes.append(FieldChange("quantity", _text(current.quantity), _text(item.quantity)))

    if current.currency and item.currency and current.currency != item.currency:
        changes.append(FieldChange("currency", current.currency, item.currency))
        needs_review = True
        reasons.append(
            f"currency changed from {current.currency} to {item.currency}; "
            f"suppliers rarely switch, so this is usually a misread symbol"
        )

    if not changes:
        return Action(
            kind="refresh",
            identity_key=item.identity_key,
            offer_id=current.id,
            incoming=item,
            reason="unchanged; still listed",
        )

    return Action(
        kind="update",
        identity_key=item.identity_key,
        offer_id=current.id,
        incoming=item,
        changes=changes,
        needs_review=needs_review,
        reason="; ".join(reasons),
    )


def _differs(old: float | None, new: float | None) -> bool:
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return abs(old - new) > 0.005


def _implausible_move(old: float | None, new: float | None) -> bool:
    if not old or not new or old <= 0 or new <= 0:
        return False
    ratio = max(old, new) / min(old, new)
    return ratio >= IMPLAUSIBLE_PRICE_FACTOR


def _text(value: object) -> str | None:
    return None if value is None else str(value)
