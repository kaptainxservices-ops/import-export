"""Run matching over the real sample emails and print what it finds.

Unit tests prove the algorithm behaves. This shows what it actually surfaces from the
client's own inbox — supply from every seller list, demand from every WTB list, matched
across suppliers.

    python scripts/demo_matching.py samples/
"""

from __future__ import annotations

import email
import sys
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brain.classify import classify_side  # noqa: E402
from app.brain.matching import (  # noqa: E402
    Requirement,
    Supply,
    fill_requirement,
    group_options,
)
from app.brain.normalise import parse_product  # noqa: E402
from app.brain.sender import resolve_sender  # noqa: E402
from app.brain.tables import parse_grid, read_html_tables, read_spreadsheet  # noqa: E402

INTERNAL = {"tvdservices.com"}
INTERNAL_ADDR = {"tvdservices@hotmail.com", "tvdlogistics@outlook.com"}


def load(path: Path):
    with path.open("rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)

    html = text = ""
    for part in msg.walk():
        if part.get_filename():
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if part.get_content_type() == "text/html" and not html:
            html = content
        elif part.get_content_type() == "text/plain" and not text:
            text = content

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

    sender = resolve_sender(
        msg.get("From"), msg.get("Subject"), text or html,
        internal_domains=INTERNAL, internal_addresses=INTERNAL_ADDR,
    )
    side = classify_side(msg.get("Subject"), text).side
    who = sender.email or sender.name or path.stem[:24]

    return who, side, rows


def main(root: Path) -> int:
    supply: list[Supply] = []
    demand: list[Requirement] = []

    for path in sorted(root.rglob("*.eml")):
        who, side, rows = load(path)
        if side is None:
            continue

        for index, row in enumerate(rows):
            spec = parse_product(row.description, ean=row.ean, colour=row.colour)
            common = dict(
                counterparty_id=who,
                quantity=row.quantity,
                unit_price=row.price,
                currency=row.currency,
                identity_key=spec.identity_key(),
                ean=spec.ean,
                brand=spec.brand,
                description=row.description,
                description_key=spec.description_key,
                match_key=spec.match_key,
                capacity_gb=spec.capacity_gb,
                colour=spec.colour,
            )
            if side == "sell":
                supply.append(Supply(id=f"{path.stem[:12]}-{index}", **common))
            else:
                demand.append(Requirement(id=f"{path.stem[:12]}-{index}", **common))

    print(f"supply: {len(supply)} offers from {len({s.counterparty_id for s in supply})} sellers")
    print(f"demand: {len(demand)} requirements from "
          f"{len({d.counterparty_id for d in demand})} buyers")

    matched = 0
    combos = 0
    shown = 0

    for requirement in demand:
        options = fill_requirement(requirement, supply)
        if not options:
            continue
        matched += 1
        if any(o.kind == "combination" for o in options):
            combos += 1

        if shown >= 3:
            continue
        shown += 1

        print(f"\n{'=' * 72}")
        print(f"BUYER WANTS  {requirement.quantity} x {requirement.description[:46]}")
        print(f"             from {requirement.counterparty_id} at "
              f"{requirement.unit_price} {requirement.currency or ''}")

        for section, items in group_options(options).items():
            print(f"\n  {section.upper()}")
            for option in items[:3]:
                legs = " + ".join(
                    f"{a.quantity}@{a.unit_price} ({a.counterparty_id[:22]})"
                    for a in option.allocations
                )
                margin = (
                    f"margin {option.total_margin} ({option.margin_pct:.1f}%)"
                    if option.total_margin is not None and option.margin_pct is not None
                    else "margin unknown"
                )
                short = f", short {option.shortfall}" if option.shortfall else ""
                print(f"    {legs}")
                print(f"      cost {option.blended_unit_cost}  {margin}{short}")
                for note in option.relaxed + option.warnings:
                    print(f"      ! {note}")

    print(f"\n{'=' * 72}")
    print(f"{matched} of {len(demand)} requirements have at least one option")
    print(f"{combos} required combining several suppliers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "samples")))
