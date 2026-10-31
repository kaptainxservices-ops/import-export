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

from dataclasses import dataclass, field, replace
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
    category: str | None = None
    # The email that first put this row on the board. Rows that arrived in one email
    # show which categories this sender lists together (see list_scope).
    source_email_id: str | None = None


@dataclass(frozen=True)
class IncomingOffer:
    """A line extracted from the list that just arrived."""

    identity_key: str
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    description: str = ""
    source_ref: str = ""
    # The sheet and section a row came from. Two rows of one identity from the same
    # place at the same price are two batches of one lot, and their quantities add.
    group: str = ""

    # Product columns — brand, ean, capacity and so on — carried through for storage.
    # Reconciliation never reads them; identity is already decided by identity_key.
    # They travel here because the database recomputes identity_key from these columns
    # as a generated column, so an insert that omits them keys every row identically
    # and the second row violates the uniqueness constraint.
    fields: dict = field(default_factory=dict)


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
    close_scope: set[str] | None = None,
    update_existing: bool = True,
) -> ReconcileResult:
    """Work out what a newly arrived list means.

    Nothing is written here — the caller applies the actions. Keeping the decision
    separate from the writing is what makes this testable, and reconciliation is the
    one place where a silent mistake is expensive.

    `is_complete_list` is deliberately three-valued. True licences closures, False
    forbids them, and **None — undecided — also forbids them**. A supplier who normally
    sends full lists will occasionally fire off "just got 50 more 15PMs", and reading
    that as a complete list would close everything else they stock.

    `update_existing=False` is for an email that arrived after newer lists from the
    same sender: it may put on lines the board has never had, but what it says about a
    line already there is older than what the board holds, so those lines are left as
    they are -- not repriced, not refreshed, not reopened.
    """
    result = ReconcileResult(row_count=len(incoming))

    deduped, duplicates, combined = _dedupe(incoming)
    result.duplicate_rows = duplicates
    if duplicates:
        result.notes.append(
            f"{duplicates} duplicate identities in one list; kept the first of each"
        )
    if combined:
        result.notes.append(
            f"{combined} repeated lines added to the line above them (same sheet, same price)"
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

    left_alone = 0
    for item in deduped:
        current = live.get(item.identity_key)

        if current is not None:
            if not update_existing:
                left_alone += 1
                continue
            result.actions.append(_compare(current, item))
            continue

        previously_closed = closed.get(item.identity_key)
        if previously_closed is not None:
            if not update_existing:
                # Closed by a newer list, quite possibly: an older email cannot reopen it.
                left_alone += 1
                continue
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

    if left_alone:
        result.notes.append(
            f"{left_alone} lines already on the board left as they were: newer lists "
            f"from this sender have said more recently what they are"
        )

    if may_close:
        # A list replaces what the sender had *in the categories it covers*. Senders
        # split their stock by category -- Mobielwerkt sends Accessories, Phones and
        # Tablets as three emails -- and the phones list says nothing about tablets.
        # Without this, each list closed the other two as sold.
        seen = {item.identity_key for item in deduped}
        for key, offer in live.items():
            if close_scope is not None and offer.id not in close_scope:
                continue
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


def _dedupe(incoming: list[IncomingOffer]) -> tuple[list[IncomingOffer], int, int]:
    """One row per identity. Returns (rows, dropped duplicates, combined repeats).

    The same product does appear twice in one list — under two section headings, or
    once with an EAN and once without. Summing those would invent stock; they are one
    lot described twice, so the first is kept.

    Two lines of one identity in the *same* sheet and section, at the same price and
    with different quantities, are the other case. Whitehawk lists 'A | SAM S25 ULTRA |
    512G | Black | 3 | 615' and, two lines later, the same with 4: two batches of one
    lot, 7 units. Keeping the first reported 3 and lost 4 real handsets.
    """
    first: dict[str, int] = {}
    out: list[IncomingOffer] = []
    duplicates = 0
    combined = 0

    for item in incoming:
        index = first.get(item.identity_key)
        if index is None:
            first[item.identity_key] = len(out)
            out.append(item)
            continue

        kept = out[index]
        repeat_batch = (
            item.group
            and item.group == kept.group
            and kept.quantity is not None
            and item.quantity is not None
            and kept.quantity != item.quantity
            and kept.unit_price is not None
            and item.unit_price is not None
            and abs(kept.unit_price - item.unit_price) < 0.005
            and (kept.currency or "") == (item.currency or "")
        )
        if repeat_batch:
            out[index] = replace(kept, quantity=kept.quantity + item.quantity)
            combined += 1
            continue

        duplicates += 1

    return out, duplicates, combined


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


# A category joins another's scope only when the sender has really listed them together:
# at least this many rows of it in one email, or this share of that email.
_LINK_MIN_ROWS = 2
_LINK_MIN_SHARE = 0.10


def list_scope(
    existing: list[ExistingOffer],
    covered: set[str | None],
    already_confirmed: set[str | None] = frozenset(),
) -> set[str]:
    """The live rows a new list speaks for: the ids it may close if they are absent.

    The categories it contains, plus any category this sender has previously sent in
    the same email as one of them. Two habits look identical from a single email:

    - one combined list a day (phones and an adapter, together): tomorrow's list with
      no adapter means the adapter sold;
    - one list per category (Mobielwerkt's Accessories, Phones and Tablets, as three
      emails): the phones list says nothing about tablets.

    The sender's own history tells them apart. A stray row does not link two
    categories -- one accessory inside a 120-line phone list is not a habit.

    Rows with no category link nothing: extraction leaves a category empty when it
    cannot tell, and an empty category shared by an accessories list and a phones list
    made the phones list close every accessory. An uncategorised row belongs with what
    the email it came in was otherwise about.

    `already_confirmed` are categories a whole list covered earlier today; this list
    adds to them but closes nothing there.
    """
    live = [o for o in existing if o.status == "live"]
    by_email: dict[str, list[ExistingOffer]] = {}
    for offer in live:
        by_email.setdefault(offer.source_email_id or f"row:{offer.id}", []).append(offer)

    real = {c for c in covered if c is not None}
    if not real:
        # A sender whose lines are all uncategorised: their uncategorised rows are the
        # list, as before categories were considered at all.
        if None in already_confirmed:
            return set()
        return {o.id for o in live if o.category is None}

    groups = {
        key: habitual_categories([o.category for o in rows]) for key, rows in by_email.items()
    }
    categories = set(real)
    for together in groups.values():
        if together & real:
            categories |= together
    categories -= {c for c in already_confirmed if c is not None}

    # A row whose category is a habit of the email it came in is judged by that
    # category. A row that was a stray there -- uncategorised, or the one 'phone' among
    # seventy accessories, which was a ring stand -- belongs with what its email was
    # about, and is closed only by a list about that.
    ids: set[str] = set()
    for key, rows in by_email.items():
        habits = groups[key]
        speaks_for_email = bool(habits & categories)
        for row in rows:
            if row.category is not None and row.category in habits:
                if row.category in categories:
                    ids.add(row.id)
            elif speaks_for_email:
                ids.add(row.id)
    return ids


def habitual_categories(categories: list[str | None]) -> set[str]:
    """The categories a list is really about: not its strays or its unknowns."""
    counts: dict[str, int] = {}
    for category in categories:
        if category is not None:
            counts[category] = counts.get(category, 0) + 1
    total = len(categories) or 1
    return {
        c for c, n in counts.items()
        if n >= _LINK_MIN_ROWS or n / total >= _LINK_MIN_SHARE
    }
