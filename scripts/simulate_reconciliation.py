"""End-to-end rehearsal of a second morning, on a real supplier list.

Unit tests check reconciliation against hand-written fixtures. This runs a genuine
price list through the whole pipeline — extract, identify, reconcile — then replays it
with realistic overnight changes, and checks the system reaches the right conclusions.

It also rehearses the failure that matters most: the same supplier's list arriving
malformed, so that only a handful of rows parse. Nothing may close in that case.

    python scripts/simulate_reconciliation.py samples/
"""

from __future__ import annotations

import email
import random
import sys
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brain.completeness import detect_list_completeness  # noqa: E402
from app.brain.normalise import parse_product  # noqa: E402
from app.brain.reconcile import ExistingOffer, IncomingOffer, reconcile  # noqa: E402
from app.brain.tables import parse_grid, read_html_tables, read_spreadsheet  # noqa: E402

random.seed(7)


def load_rows(path: Path):
    with path.open("rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)

    html = ""
    for part in msg.walk():
        if part.get_content_type() == "text/html" and not part.get_filename():
            html = part.get_content()
            break

    grids = read_html_tables(html)
    for part in msg.walk():
        name = part.get_filename()
        if name and name.lower().endswith((".xlsx", ".xls", ".xlsm")):
            payload = part.get_payload(decode=True)
            if payload:
                grids.extend(read_spreadsheet(payload, name))

    rows = []
    for grid in grids:
        table = parse_grid(grid, decimal_hint="comma")
        rows.extend(table.rows)

    return msg.get("Subject", ""), rows


def to_incoming(rows) -> list[IncomingOffer]:
    out = []
    for row in rows:
        spec = parse_product(row.description, ean=row.ean, colour=row.colour)
        out.append(
            IncomingOffer(
                identity_key=spec.identity_key(),
                quantity=row.quantity,
                unit_price=row.price,
                currency=row.currency,
                description=row.description,
                source_ref=row.source_ref,
            )
        )
    return out


def as_existing(items: list[IncomingOffer]) -> list[ExistingOffer]:
    return [
        ExistingOffer(
            id=f"offer-{i}",
            identity_key=item.identity_key,
            quantity=item.quantity,
            unit_price=item.unit_price,
            currency=item.currency,
        )
        for i, item in enumerate(items)
    ]


def _dedupe_for_board(items: list[IncomingOffer]) -> list[IncomingOffer]:
    """The board holds one row per identity, so the rehearsal must build it that way."""
    seen, out = set(), []
    for item in items:
        if item.identity_key in seen:
            continue
        seen.add(item.identity_key)
        out.append(item)
    return out


def overnight(items: list[IncomingOffer]) -> list[IncomingOffer]:
    """A plausible next morning: some sold, some repriced, a few new."""
    kept = [i for i in items if random.random() > 0.08]           # ~8% sold

    changed = []
    for item in kept:
        if item.unit_price and random.random() < 0.15:            # ~15% repriced
            changed.append(
                IncomingOffer(
                    identity_key=item.identity_key,
                    quantity=item.quantity,
                    unit_price=round(item.unit_price * random.uniform(0.94, 1.06), 2),
                    currency=item.currency,
                    description=item.description,
                )
            )
        else:
            changed.append(item)

    for n in range(4):                                            # a few new lines
        changed.append(
            IncomingOffer(
                identity_key=f"ean:999000000000{n}",
                quantity=25,
                unit_price=699.0,
                currency="EUR",
                description=f"New arrival {n}",
            )
        )

    return changed


def report(title: str, result) -> None:
    print(f"\n  {title}")
    print(f"    status      {result.status}")
    print(
        f"    refreshed {result.refreshed:5}   updated {result.updated:5}   "
        f"inserted {result.inserted:5}   closed {result.closed:5}"
    )
    if result.needing_review:
        print(f"    flagged for review: {result.needing_review}")
    for note in result.notes:
        print(f"    note: {note}")


def main(root: Path) -> int:
    candidates = sorted(root.rglob("*.eml"))
    if not candidates:
        print(f"no .eml files under {root}")
        return 1

    best: tuple[int, Path, str, list] | None = None
    for path in candidates:
        subject, rows = load_rows(path)
        if best is None or len(rows) > best[0]:
            best = (len(rows), path, subject, rows)

    assert best is not None
    count, path, subject, rows = best
    if count < 20:
        print("no list large enough to rehearse against")
        return 1

    print(f"Supplier list: {path.name}")
    print(f"Subject:       {subject}")
    print(f"Rows parsed:   {count}")

    day_one = to_incoming(rows)
    complete, why = detect_list_completeness(subject, "", len(day_one))
    print(f"Completeness:  {complete}  ({why})")

    # Day one: an empty board.
    first = reconcile([], day_one, is_complete_list=complete)
    report("DAY 1 — first import into an empty board", first)

    board = as_existing(_dedupe_for_board(day_one))

    # Day two: the same supplier, a normal morning.
    day_two = overnight(day_one)
    second = reconcile(
        board, day_two, previous_row_count=len(day_one), is_complete_list=complete
    )
    report("DAY 2 — normal morning", second)

    # Day two, alternative: the attachment is malformed and barely anything parses.
    broken = day_two[:8]
    third = reconcile(
        board, broken, previous_row_count=len(day_one), is_complete_list=complete
    )
    report("DAY 2 — same list arrives malformed, only 8 rows parse", third)

    print()
    if third.closed:
        print(f"  FAIL: a broken parse closed {third.closed} live offers")
        return 1

    print(f"  PASS: the broken parse closed nothing "
          f"(it would otherwise have closed {len(board) - 8} live offers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "samples")))
