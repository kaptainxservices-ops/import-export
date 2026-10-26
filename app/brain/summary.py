"""A summary sheet is not more stock.

Whitehawk's workbook carries the same stock twice: a colour-wise sheet ('HONOR MAGIC
PAD 4 ... WHITE 2', '... GRAY 10') and a model-wise sheet that totals it ('HONOR MAGIC
PAD 4 ... 12'). Both are honest; only one of them is the list. Read as two lists, every
product reached the board twice -- once with a colour and once without -- and matching
offered the trader the same 12 tablets as two separate lots.

A row of one sheet is a summary when other sheets of the same email carry the same
product at the same price, split by colour, and their quantities add up to it. A sheet
is treated as a summary only when most of its rows are, and only rows that are covered
are dropped -- a line the detail sheet does not have stays, because a missing row is
worse than a duplicate one.
"""

from __future__ import annotations

from collections import defaultdict

from app.brain.normalise.product import build_description_key, parse_product
from app.brain.tables.parser import TableRow

# Below this share of covered rows a sheet is a different list that happens to overlap.
SUMMARY_SHARE = 0.6
MIN_COVERED = 3


def grid_of(row: TableRow) -> str:
    return row.source_ref.rsplit(" row ", 1)[0]


def _key(row: TableRow) -> tuple:
    spec = parse_product(row.description, capacity=row.capacity, ram=row.ram)
    price = round(row.price, 2) if row.price is not None else None
    return (
        build_description_key(row.description),
        spec.capacity_gb,
        price,
        (row.currency or "").upper(),
        row.availability or "",
        (row.grade or "").upper(),
    )


def drop_summary_rows(rows: list[TableRow]) -> tuple[list[TableRow], int]:
    """Remove rows that only total other rows of the same email. Returns (rows, dropped)."""
    grids: dict[str, list[TableRow]] = defaultdict(list)
    for row in rows:
        grids[grid_of(row)].append(row)
    if len(grids) < 2:
        return rows, 0

    keys = {id(row): _key(row) for row in rows}
    drop: set[int] = set()

    for name, members in grids.items():
        # A summary sheet has no colour column; the sheet it summarises does.
        if any(r.colour for r in members):
            continue

        detail: dict[tuple, list[TableRow]] = defaultdict(list)
        for other_name, others in grids.items():
            if other_name == name or not any(r.colour for r in others):
                continue
            for other in others:
                detail[keys[id(other)]].append(other)
        if not detail:
            continue

        covered = []
        for row in members:
            matches = detail.get(keys[id(row)])
            if not matches:
                continue
            if row.quantity is None:
                covered.append(row)
                continue
            quantities = [m.quantity for m in matches if m.quantity is not None]
            if quantities and sum(quantities) == row.quantity:
                covered.append(row)

        if len(covered) >= MIN_COVERED and len(covered) >= SUMMARY_SHARE * len(members):
            drop.update(id(r) for r in covered)

    kept = [r for r in rows if id(r) not in drop]
    return kept, len(rows) - len(kept)
