"""Run a supplier's most recent stored email through extraction again.

For checking a parser change against real mail without asking the supplier to resend:

    python scripts/reprocess_latest.py --from pooja@whitehawkltd.com --tenant-name "TVD Services"
    python scripts/reprocess_latest.py --from pooja@whitehawkltd.com --tenant <uuid> --dry-run

The stored email is read back -- body and attachment rows exactly as they arrived --
and goes through the same `reprocess_email` path a person triggers from the Review
tab. Nothing about the supplier or the side of the trade is re-decided.

The same-day rule still applies, deliberately. That supplier's list was already applied
today, so a re-read that differs from it is *held* for a person rather than applied:
open Review on the dashboard, read the difference, and choose. `--dry-run` prints what
would be read without writing anything.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brain.summary import drop_summary_rows  # noqa: E402
from app.db.supabase_repo import SupabaseRepository, get_client  # noqa: E402
from app.pipeline import _spec, extract_rows, reprocess_email  # noqa: E402
from app.schemas.email import Attachment, InboundEmail  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="sender", required=True, help="supplier's address")
    parser.add_argument("--tenant", help="tenant UUID")
    parser.add_argument("--tenant-name", help="tenant name, looked up instead of the UUID")
    parser.add_argument("--dry-run", action="store_true", help="read only, write nothing")
    args = parser.parse_args()
    if not args.tenant and not args.tenant_name:
        parser.error("give either --tenant or --tenant-name")

    client = get_client()
    repo = SupabaseRepository(client)

    tenant_id = args.tenant
    if not tenant_id:
        rows = (
            client.table("tenants").select("id,name")
            .ilike("name", f"%{args.tenant_name}%").execute()
        ).data
        if len(rows) != 1:
            names = ", ".join(r["name"] for r in rows) or "none"
            print(f"tenant name must match exactly one tenant; matched: {names}")
            return 1
        tenant_id = rows[0]["id"]

    emails = (
        client.table("emails").select("id,subject,received_at,from_email")
        .eq("tenant_id", tenant_id).ilike("from_email", args.sender)
        .order("received_at", desc=True).limit(1).execute()
    ).data
    if not emails:
        print(f"no stored email from {args.sender} on tenant {tenant_id}")
        return 1
    email = emails[0]
    print(f"email {email['id']}  {email['received_at']}  {email['subject']!r}")

    stored = repo.get_stored_email(tenant_id, email["id"])
    payload = InboundEmail(
        tenant_id=tenant_id, message_id=stored.message_id, from_email=stored.from_email,
        subject=stored.subject, received_at=stored.received_at, body_text=stored.body_text,
        body_raw=stored.body_raw, attachments=[Attachment(**a) for a in stored.attachments],
    )
    rows, _, unmapped = extract_rows(payload, None, None, None)
    rows, summarised = drop_summary_rows(rows)
    sheets = Counter(r.source_ref.rsplit(" row ", 1)[0] for r in rows)
    for sheet, count in sheets.items():
        print(f"  {sheet}: {count} rows")
    specs = [_spec(r) for r in rows]
    print(
        f"  read {len(rows)} rows; summary rows left out {summarised}; "
        f"no qty {sum(r.quantity is None for r in rows)}; "
        f"graded {sum(bool(s.grade) for s in specs)}; "
        f"incoming {sum(s.availability == 'incoming' for s in specs)}; "
        f"currencies {dict(Counter(r.currency for r in rows))}; unmapped tables {unmapped}"
    )

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    result = reprocess_email(tenant_id, email["id"], repo)
    print(f"outcome: {result.outcome}, {result.row_count} rows")
    for note in result.notes:
        print(f"  - {note}")
    if result.outcome == "held_for_review":
        print("held: open the Review tab on the dashboard to compare and apply it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
