"""What changed between the board and a list that was held back.

This is what a person is shown when a supplier sends a second list the same day and the
system refuses to guess what it means. Three answers are possible — it is the same list,
it is a new list, or parts of it are new — and the third needs the differences laid out
row by row before anyone can pick.

The shape is deliberately not 'old rows' and 'new rows' side by side. A 450-row list
against a 450-row board is 900 rows to read, and the answer is usually that nine things
moved. So the output is sorted by how much a trader needs to look at it: rows that
appeared, rows that vanished, rows whose price moved, rows whose quantity moved, and
then everything that stayed still — which is almost all of it, and which the screen can
collapse.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

# Ordered by how much they deserve a person's attention. `unchanged` last on purpose:
# it is the bulk of any real list and the least interesting thing in it.
KINDS = ("appeared", "vanished", "price", "quantity", "unchanged")


class Row(Protocol):
    identity_key: str
    quantity: int | None
    unit_price: float | None
    currency: str | None


@dataclass(frozen=True)
class Change:
    kind: str
    identity_key: str
    description: str = ""

    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None

    previous_quantity: int | None = None
    previous_price: float | None = None

    # The live offer this row would act on, where there is one. Null for `appeared`.
    offer_id: str | None = None

    @property
    def price_move(self) -> float | None:
        """How far the price moved, as a fraction. None when either side is missing.

        A trader reads 1.8% and 40% differently, and the second is usually a parse
        error rather than a reprice — the same signal `IMPLAUSIBLE_PRICE_FACTOR`
        watches for during an ordinary reconcile.
        """
        if self.previous_price in (None, 0) or self.unit_price is None:
            return None
        return (self.unit_price - self.previous_price) / self.previous_price


@dataclass
class Diff:
    changes: list[Change]

    def of(self, kind: str) -> list[Change]:
        return [change for change in self.changes if change.kind == kind]

    @property
    def counts(self) -> dict[str, int]:
        return {kind: len(self.of(kind)) for kind in KINDS}

    @property
    def material(self) -> list[Change]:
        """Everything except the rows that stayed still."""
        return [change for change in self.changes if change.kind != "unchanged"]

    @property
    def summary(self) -> str:
        counts = self.counts
        parts = [
            f"{counts['appeared']} new" if counts["appeared"] else "",
            f"{counts['vanished']} gone" if counts["vanished"] else "",
            f"{counts['price']} repriced" if counts["price"] else "",
            f"{counts['quantity']} requantified" if counts["quantity"] else "",
        ]
        said = ", ".join(part for part in parts if part)
        return said or "identical"


def _describe(row) -> str:
    return getattr(row, "description", "") or ""


def compare(live: Iterable[Row], arriving: Iterable[Row]) -> Diff:
    """What the arriving list would do to the board, row by row.

    `vanished` is the one to read carefully. It means the row is on the board and absent
    from this list — which during an ordinary reconcile would close it as sold. Here it
    closes nothing: a held list is a question, and this is the part of the question a
    person is least able to answer from memory, so it is listed rather than acted on.
    """
    before = {row.identity_key: row for row in live}
    after = {row.identity_key: row for row in arriving}

    changes: list[Change] = []

    for key, row in after.items():
        previous = before.get(key)

        if previous is None:
            changes.append(
                Change(
                    kind="appeared",
                    identity_key=key,
                    description=_describe(row),
                    quantity=row.quantity,
                    unit_price=row.unit_price,
                    currency=row.currency,
                )
            )
            continue

        moved_price = row.unit_price != previous.unit_price
        moved_quantity = row.quantity != previous.quantity

        # A row whose price AND quantity both moved is reported as a price change:
        # price is what a trader quotes on, and listing it twice would double-count it
        # in every total on the screen.
        if moved_price:
            kind = "price"
        elif moved_quantity:
            kind = "quantity"
        else:
            kind = "unchanged"

        changes.append(
            Change(
                kind=kind,
                identity_key=key,
                description=_describe(row) or _describe(previous),
                quantity=row.quantity,
                unit_price=row.unit_price,
                currency=row.currency,
                previous_quantity=previous.quantity,
                previous_price=previous.unit_price,
                offer_id=getattr(previous, "id", None),
            )
        )

    for key, row in before.items():
        if key in after:
            continue
        changes.append(
            Change(
                kind="vanished",
                identity_key=key,
                description=_describe(row),
                previous_quantity=row.quantity,
                previous_price=row.unit_price,
                currency=row.currency,
                offer_id=getattr(row, "id", None),
            )
        )

    order = {kind: index for index, kind in enumerate(KINDS)}
    changes.sort(key=lambda change: (order[change.kind], change.identity_key))
    return Diff(changes=changes)
