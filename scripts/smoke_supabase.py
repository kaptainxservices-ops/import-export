"""Prove the pipeline works against the real database, then clean up after itself.

Everything so far is verified against an in-memory store. That checks the logic but not
the storage layer, and the storage layer is where the mismatches live: a column that
does not exist, an enum value spelled differently, a timestamp sent as text, a
constraint that fires on the second row rather than the first.

This runs a genuine supplier email end to end into your Supabase project — twice, so
reconciliation is exercised too — checks what landed, and deletes everything it made.

    python scripts/smoke_supabase.py samples/fwallareofferemail/Fwd_ PRICELIST FROM 27.07.2026.eml

Reads SUPABASE_URL and SUPABASE_SECRET_KEY from .env. It creates a tenant named
SMOKE TEST and removes it at the end, including everything that cascades from it.
"""

from __future__ import annotations

import email
import sys
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.supabase_repo import SupabaseRepository, get_client  # noqa: E402
from app.pipeline import process_email  # noqa: E402
from app.schemas.email import InboundEmail  # noqa: E402

TENANT_NAME = "SMOKE TEST — safe to delete"


def load_email(path: Path) -> tuple[str, str, str]:
    with path.open("rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)

    html = ""
    text = ""
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

    return msg.get("Subject", ""), text, html


def main(sample: Path) -> int:
    if not sample.exists():
        print(f"no such file: {sample}")
        return 1

    client = get_client()
    repo = SupabaseRepository(client)
    tenant_id = None

    try:
        print("1. creating a throwaway tenant")
        tenant_id = (
            client.table("tenants")
            .insert({"name": TENANT_NAME, "internal_domains": ["tvdservices.com"]})
            .execute()
        ).data[0]["id"]
        print(f"   tenant {tenant_id}")

        subject, text, html = load_email(sample)
        print(f"2. loaded {sample.name}\n   subject: {subject[:60]}")

        def payload(message_id: str) -> InboundEmail:
            return InboundEmail(
                tenant_id=tenant_id,
                message_id=message_id,
                from_email="smoke-supplier@example.test",
                from_name="Smoke Supplier",
                subject=subject,
                received_at="2026-08-07T09:00:00Z",
                body_text=text,
                body_raw=html or text,
            )

        print("3. first import")
        first = process_email(payload("<smoke-1@test>"), repo)
        _report(first)
        if not first.reconcile or first.reconcile.inserted == 0:
            print("   FAIL: nothing was inserted")
            return 1

        print("4. same list again — should refresh, not duplicate or close")
        second = process_email(payload("<smoke-2@test>"), repo)
        _report(second)
        if second.reconcile.inserted or second.reconcile.closed:
            print("   FAIL: a repeated list should only refresh")
            return 1

        print("5. redelivery of the same message — should be ignored")
        third = process_email(payload("<smoke-2@test>"), repo)
        if third.outcome != "duplicate":
            print(f"   FAIL: expected 'duplicate', got {third.outcome!r}")
            return 1
        print("   ignored, as intended")

        print("6. checking what actually landed")
        offers = (
            client.table("offers")
            .select("id,description,quantity,unit_price,currency,ean,brand,identity_key,status")
            .eq("tenant_id", tenant_id)
            .limit(3)
            .execute()
        ).data
        total = (
            client.table("offers").select("id", count="exact")
            .eq("tenant_id", tenant_id).execute()
        ).count

        print(f"   {total} offers stored")
        for row in offers:
            print(
                f"     {row['description'][:38]:38} qty={row['quantity']} "
                f"{row['unit_price']} {row['currency']} ean={row['ean']}"
            )
            print(f"       identity_key={row['identity_key'][:60]}")

        blank = [o for o in offers if o["identity_key"] in ("spec:|||", "")]
        if blank:
            print("   FAIL: identity_key is empty — the product columns did not arrive")
            return 1

        imports = (
            client.table("imports").select("row_count,status,offers_inserted,offers_refreshed")
            .eq("tenant_id", tenant_id).execute()
        ).data
        print(f"   {len(imports)} imports recorded: {imports}")

        print("\n   PASS: the pipeline works against the real database")
        return 0

    finally:
        if tenant_id:
            print("\n7. cleaning up")
            client.table("tenants").delete().eq("id", tenant_id).execute()
            print("   throwaway tenant deleted (offers, emails and imports cascade)")


def _report(result) -> None:
    if result.reconcile is None:
        print(f"   outcome={result.outcome} notes={result.notes}")
        return
    r = result.reconcile
    print(
        f"   outcome={result.outcome} side={result.side} rows={result.row_count} "
        f"inserted={r.inserted} updated={r.updated} refreshed={r.refreshed} "
        f"closed={r.closed} status={r.status}"
    )


if __name__ == "__main__":
    default = Path("samples/fwallareofferemail/Fwd_ PRICELIST FROM 27.07.2026.eml")
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else default))
