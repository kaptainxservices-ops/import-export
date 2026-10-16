"""What a list says, reduced to one stable string.

Two problems turn out to be the same problem.

A supplier addresses one email to `info@` and `sales@`. Both forward to their own
collector, so it arrives twice. Message-ID normally settles that — one message, one id,
preserved by Gmail auto-forward and by Outlook *Redirect*. But Outlook *Forward* wraps
the original in a new message with a **new** Message-ID, and then the two copies look
like two emails. The second one reconciles the first one's rows all over again.

Separately, a supplier sends their list again the same day with nothing changed. The
client wants a person to decide what a second same-day list means — except when it says
exactly what the first one said, where there is nothing to decide.

Both are answered by asking a narrower question than "is this the same email": **does
this list say the same thing as the one we already hold?** Two deliveries of one email
say the same thing. An unchanged resend says the same thing. Neither should move the
board, and the action for both is identical — refresh the timestamps, reconcile nothing.

So the fingerprint covers what would change the board and nothing else:

    identity, quantity, price, currency

Description is deliberately out. A supplier who retypes 'iPhone 16 128GB Black' as
'Apple iPhone 16 128Gb black' has changed nothing a broker would act on, and treating
that as a new list would send a person to compare two identical boards. Source
references are out for the same reason twice over — the same workbook forwarded from two
mailboxes arrives under two filenames.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Protocol


class Priced(Protocol):
    """The part of an offer that the board shows and a trader acts on."""

    identity_key: str
    quantity: int | None
    unit_price: float | None
    currency: str | None


def _money(value: float | None) -> str:
    """Prices arrive as floats and 519.0 must not differ from 519.

    Rounded to the cent because that is the resolution anyone quotes in, and because
    0.1 + 0.2 is not 0.3 in binary floating point — a list re-parsed from the same bytes
    can otherwise fingerprint differently from itself.
    """
    return "" if value is None else f"{round(value + 0.0, 2):.2f}"


def row_fingerprint(row: Priced) -> str:
    return "|".join(
        [
            row.identity_key,
            "" if row.quantity is None else str(row.quantity),
            _money(row.unit_price),
            (row.currency or "").upper(),
        ]
    )


def list_fingerprint(rows: Iterable[Priced]) -> str:
    """One hex digest for a whole list.

    Sorted, because row order is not information: the same workbook read twice can
    present its sheets in a different order, and a supplier who sorts by price today and
    by brand tomorrow has not changed their stock.

    Counted, because sorting alone would make a list and the same list with one row
    duplicated agree — and a duplicated row is exactly the kind of parse change worth
    noticing.
    """
    lines = sorted(row_fingerprint(row) for row in rows)
    digest = hashlib.sha256()
    digest.update(str(len(lines)).encode())
    for line in lines:
        digest.update(b"\n")
        digest.update(line.encode())
    return digest.hexdigest()


def describe_difference(before: Iterable[Priced], after: Iterable[Priced]) -> str:
    """One line saying how two lists differ, for the review screen and the import note.

    Not a diff — `app/brain/listdiff.py` does that. This is the sentence that tells
    someone whether opening the diff is worth it.
    """
    left = {row.identity_key: row for row in before}
    right = {row.identity_key: row for row in after}

    added = len(right.keys() - left.keys())
    gone = len(left.keys() - right.keys())
    changed = sum(
        1
        for key in left.keys() & right.keys()
        if row_fingerprint(left[key]) != row_fingerprint(right[key])
    )

    if not (added or gone or changed):
        return "identical"

    parts = []
    if added:
        parts.append(f"{added} new")
    if gone:
        parts.append(f"{gone} gone")
    if changed:
        parts.append(f"{changed} changed")
    return ", ".join(parts)
