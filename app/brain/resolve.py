"""Settling a list that was held back.

Three answers, and they are not three shades of the same action — they differ in
whether absence is allowed to mean anything, which is the only question in this system
that destroys data when it is answered wrongly.

    same     nothing in this list is news
    new      this IS the supplier's current stock
    partial  these particular rows are news, the rest I am not vouching for

`new` is the only one that may close. Saying 'this is a new list' is exactly the
statement reconciliation needs: the list is complete, so a row absent from it has sold.
A person made that statement deliberately, which is the one circumstance where closure
is licensed.

`partial` must never close, however tempting the symmetry. Someone ticking four rows out
of 450 is vouching for four rows. The other 446 were not examined, and reading their
absence from a selection as 'sold' would turn a careful, narrow action into the widest
possible one.

`same` is not quite 'do nothing'. The supplier has just confirmed this stock is current,
so the rows are touched — timestamps only, no price and no quantity. Without that the
board ages a list its owner has just stood behind.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.brain.reconcile import Action, IncomingOffer, ReconcileResult, reconcile

RESOLUTIONS = ("same", "new", "partial")


@dataclass
class Resolution:
    """What a person's answer comes to in actions."""

    resolution: str
    actions: list[Action]
    refresh_keys: list[str]
    note: str
    may_close: bool = False


def _as_incoming(rows: list[dict]) -> list[IncomingOffer]:
    return [
        IncomingOffer(
            identity_key=row["identity_key"],
            quantity=row.get("quantity"),
            unit_price=(
                float(row["unit_price"]) if row.get("unit_price") is not None else None
            ),
            currency=row.get("currency"),
            description=row.get("description") or "",
            source_ref=row.get("source_ref") or "",
            fields=row.get("fields") or {},
        )
        for row in rows
    ]


def settle(
    resolution: str,
    *,
    held: list[dict],
    live: list,
    chosen_keys: list[str] | None = None,
) -> Resolution:
    """Turn an answer into the actions that carry it out.

    Nothing is written here. The caller applies the result, which keeps this testable
    and keeps the decision in one readable place — the same split reconcile() uses, and
    for the same reason.
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution {resolution!r}")

    incoming = _as_incoming(held)

    if resolution == "same":
        # Confirmed, not changed. Only rows the board already holds are touched: a row
        # that is on this list and not on the board has never been approved by anyone,
        # and 'same' is not the answer that approves it.
        existing = {offer.identity_key for offer in live}
        keys = [row.identity_key for row in incoming if row.identity_key in existing]
        return Resolution(
            resolution="same",
            actions=[],
            refresh_keys=keys,
            note=f"confirmed as unchanged; {len(keys)} rows marked seen",
        )

    if resolution == "new":
        # The full reconcile, closures included. `is_complete_list=True` is the person's
        # statement, not an inference — which is the only basis on which this system
        # closes anything.
        result: ReconcileResult = reconcile(
            live,
            incoming,
            previous_row_count=len(live) or None,
            is_complete_list=True,
        )
        return Resolution(
            resolution="new",
            actions=result.actions,
            refresh_keys=[],
            note=(
                f"applied as a new list: {result.inserted} new, {result.updated} updated, "
                f"{result.closed} closed"
            ),
            may_close=True,
        )

    # partial
    wanted = set(chosen_keys or [])
    if not wanted:
        return Resolution(
            resolution="partial",
            actions=[],
            refresh_keys=[],
            note="no rows chosen; nothing applied",
        )

    by_key = {offer.identity_key: offer for offer in incoming}
    existing = {offer.identity_key: offer for offer in live}

    actions: list[Action] = []
    for key in sorted(wanted):
        offer = by_key.get(key)
        if offer is None:
            # A key that is not in the held list cannot be applied from it. Silently
            # skipping is right: the screen may be a version behind, and inventing a
            # row from a stale selection is worse than ignoring it.
            continue

        current = existing.get(key)
        if current is None:
            actions.append(Action(kind="insert", identity_key=key, incoming=offer))
        else:
            actions.append(
                Action(
                    kind="update",
                    identity_key=key,
                    offer_id=current.id,
                    incoming=offer,
                    reason="applied from a held list by hand",
                )
            )

    # No closures, deliberately. See the module docstring: a selection of four rows is a
    # statement about four rows.
    return Resolution(
        resolution="partial",
        actions=actions,
        refresh_keys=[],
        note=f"applied {len(actions)} of {len(held)} rows by hand; nothing closed",
    )
