"""What a deal may and may not commit to.

The specification names one rule twice, which is unusual enough to take seriously:

    Allocations cannot exceed available quantity. The system must block assigning 121 of
    a 120 lot, and warn if the same lot is committed to a second deal.

Those two halves are deliberately different strengths, and getting them the same way
round is the whole point of this module.

**Over-allocating one lot is refused.** Promising 121 units of a 120-unit lot is not a
judgement call — one of those units does not exist, and the trader finds out when a
buyer's shipment arrives short.

**Committing the same lot twice is warned about, not blocked.** It is a normal, deliberate
thing to do. A broker chasing two buyers for the same lot expects to lose one of them, and
a system that refuses would be a system he works around. What he cannot afford is doing it
by accident.

Nothing here touches storage. Given the numbers, it answers whether a commitment is
allowed — which is what makes it testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Commitment:
    """One existing claim on a lot."""

    deal_id: str
    deal_reference: int
    quantity: int
    is_open: bool = True


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    remaining: int | None = None


def check_allocation(
    *,
    wanted: int,
    lot_quantity: int | None,
    existing: list[Commitment],
    deal_id: str | None = None,
) -> Verdict:
    """Whether this deal may commit `wanted` units of a lot.

    `existing` is every other commitment against the same lot. Commitments belonging to
    `deal_id` are excluded, so editing a leg from 40 to 50 is measured against the other
    deals rather than against itself — otherwise raising an allocation is impossible.

    A lot whose quantity is unknown is allowed through with a warning. Plenty of real
    offers arrive with no quantity at all, and refusing to let the trader record a deal
    because a supplier was vague is the system getting in the way of the business.
    """
    if wanted <= 0:
        return Verdict(False, "a quantity must be a positive number")

    others = [c for c in existing if c.deal_id != deal_id]
    open_elsewhere = sum(c.quantity for c in others if c.is_open)

    if lot_quantity is None:
        return Verdict(
            True,
            remaining=None,
            warnings=(
                [f"this lot has no stated quantity — {open_elsewhere} already committed elsewhere"]
                if open_elsewhere
                else ["this lot has no stated quantity, so nothing checks the total"]
            ),
        )

    remaining = lot_quantity - open_elsewhere

    if wanted > remaining:
        if open_elsewhere:
            return Verdict(
                False,
                f"only {max(remaining, 0)} of the {lot_quantity} available are uncommitted — "
                f"{open_elsewhere} are already promised on other open deals",
                remaining=max(remaining, 0),
            )
        return Verdict(
            False,
            f"the lot is {lot_quantity} units; {wanted} cannot be promised from it",
            remaining=lot_quantity,
        )

    warnings = []
    if open_elsewhere:
        # Allowed, and said out loud. Chasing two buyers for one lot is deliberate; doing
        # it without noticing is how a broker sells the same units twice.
        references = ", ".join(f"#{c.deal_reference}" for c in others if c.is_open)
        warnings.append(
            f"this lot is already committed on {references} — "
            f"{open_elsewhere + wanted} of {lot_quantity} would be promised"
        )

    return Verdict(True, remaining=remaining - wanted, warnings=warnings)


def stale_after_days(status_updated_at, now, threshold_days: int = 8) -> int | None:
    """Days since anyone touched the deal, once past the threshold.

    A nudge, not a stage model. The client rejected pipeline stages on the grounds that
    he would not maintain them; what he will notice is a deal nobody has mentioned in
    over a week.
    """
    if status_updated_at is None:
        return None

    days = (now - status_updated_at).days
    return days if days >= threshold_days else None
