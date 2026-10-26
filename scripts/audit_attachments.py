"""Show exactly what the pipeline reads out of one or more spreadsheets.

    python scripts/audit_attachments.py samples/whitehawk-2026-10-26/*.xlsx
    python scripts/audit_attachments.py file.xlsx --rows        # every row, not just totals
    python scripts/audit_attachments.py file.xlsx --csv out.csv

Runs the same code path as a live email — attachment conversion, table parsing,
product parsing, identity and the one-identity-per-list rule — without a database or
a model. It is the quickest way to answer "did we read this supplier's list
correctly?" before the list ever reaches the board.
"""

from __future__ import annotations

import argparse
import base64
import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brain.reconcile import IncomingOffer, _dedupe  # noqa: E402
from app.brain.summary import drop_summary_rows  # noqa: E402
from app.pipeline import _spec, extract_rows, materialise_attachments  # noqa: E402
from app.schemas.email import Attachment, InboundEmail  # noqa: E402


def load(paths: list[Path]) -> InboundEmail:
    attachments = [
        Attachment(
            filename=path.name,
            content_base64=base64.b64encode(path.read_bytes()).decode(),
        )
        for path in paths
    ]
    return InboundEmail(
        tenant_id="audit",
        message_id="<audit>",
        from_email="audit@example.com",
        subject="stock list",
        received_at=datetime.now(timezone.utc),  # noqa: UP017
        body_text="",
        attachments=materialise_attachments(attachments),
    )


def _missing_capacity(record: dict) -> bool:
    return record["capacity_gb"] is None and record["category"] in ("phone", "tablet")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--rows", action="store_true", help="print every extracted row")
    parser.add_argument("--csv", type=Path, help="write every extracted row to a CSV")
    args = parser.parse_args()

    payload = load(args.files)
    rows, _, unmapped = extract_rows(payload, None, None, None)
    rows, summarised = drop_summary_rows(rows)

    records = []
    incoming = []
    for row in rows:
        spec = _spec(row)
        key = spec.identity_key()
        incoming.append(IncomingOffer(
            identity_key=key, quantity=row.quantity, unit_price=row.price,
            group=f"{row.source_ref.rsplit(' row ', 1)[0]}|{row.section or ''}",
            currency=row.currency, description=row.description, source_ref=row.source_ref,
        ))
        records.append({
            "source": row.source_ref,
            "description": row.description,
            "brand": spec.brand or row.brand,
            "category": spec.category or row.category,
            "capacity_gb": spec.capacity_gb,
            "ram_gb": spec.ram_gb,
            "colour": spec.colour,
            "grade": spec.grade,
            "region": spec.region_code,
            "availability": getattr(spec, "availability", None),
            "qty": row.quantity,
            "price": row.price,
            "currency": row.currency,
            "model_key": getattr(spec, "model_key", ""),
            "identity_key": key,
        })

    kept, duplicates, combined = _dedupe(incoming)
    kept_by_ref = {item.source_ref: item for item in kept}
    for record in records:
        record["kept"] = record["source"] in kept_by_ref
        if record["kept"]:
            record["qty"] = kept_by_ref[record["source"]].quantity

    by_sheet = Counter(r["source"].rsplit(" row ", 1)[0] for r in records)
    print(f"tables needing a model to map: {unmapped}")
    for sheet, n in by_sheet.items():
        sheet_rows = [r for r in records if r["source"].startswith(sheet + " row")]
        print(
            f"{sheet}: {n} rows read, {sum(r['kept'] for r in sheet_rows)} kept, "
            f"no qty {sum(r['qty'] is None for r in sheet_rows)}, "
            f"no price {sum(r['price'] is None for r in sheet_rows)}, "
            f"no capacity {sum(_missing_capacity(r) for r in sheet_rows)}, "
            f"currencies {dict(Counter(r['currency'] for r in sheet_rows))}"
        )
    print(f"total rows {len(records)}, distinct identities {len(kept)}, "
          f"collapsed as duplicates {duplicates}, repeat batches added {combined}, "
          f"summary-sheet rows left out {summarised}")

    if args.rows:
        for r in records:
            flag = " " if r["kept"] else "x"
            print(f"{flag} {r['source']:<60} {r['description'][:48]:<48} "
                  f"{r['capacity_gb'] or '':>5} {str(r['colour'] or '')[:14]:<14} "
                  f"g={r['grade'] or '':<3} reg={r['region'] or '':<6} "
                  f"{r['availability'] or '':<8} q={r['qty']} {r['currency']} {r['price']} "
                  f"{r['model_key']}")

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
