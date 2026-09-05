"""Push the sample emails through the real pipeline into a real tenant.

This is how the board gets something on it before n8n exists. Every email goes through
exactly the path a live one will — resolve sender, classify, extract, reconcile,
persist — so what appears on the dashboard is what the system genuinely produces, not a
fixture someone typed.

    python scripts/load_samples.py --tenant-name "TVD Services (test)"
    python scripts/load_samples.py --tenant-name "TVD Services (test)" --wipe
    python scripts/load_samples.py --tenant <uuid> --limit 5

`--wipe` clears that tenant's emails, offers and imports first. Worth using between
runs: without it the second run is treated as a genuine second morning, and rows absent
from the samples get closed as sold — correct behaviour, confusing demo.
"""

from __future__ import annotations

import argparse
import email
import sys
from collections import Counter
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.supabase_repo import SupabaseRepository, get_client  # noqa: E402
from app.pipeline import process_email  # noqa: E402
from app.schemas.email import Attachment, InboundEmail  # noqa: E402


def read_email(path: Path, tenant_id: str) -> InboundEmail:
    with path.open("rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)

    text = html = ""
    attachments: list[Attachment] = []

    for part in msg.walk():
        filename = part.get_filename()

        if filename:
            if filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
                payload = part.get_payload(decode=True)
                if payload:
                    attachments.append(_spreadsheet(filename, payload))
            continue

        try:
            content = part.get_content()
        except Exception:
            continue

        if part.get_content_type() == "text/plain" and not text:
            text = content
        elif part.get_content_type() == "text/html" and not html:
            html = content

    return InboundEmail(
        tenant_id=tenant_id,
        message_id=msg.get("Message-ID") or f"<sample-{path.stem}@local>",
        from_email=_address(msg.get("From")),
        from_name=(msg.get("From") or "").split("<")[0].strip().strip('"'),
        subject=msg.get("Subject") or path.stem,
        received_at=_received_at(msg.get("Date")),
        body_text=text,
        body_raw=html or text,
        attachments=attachments,
    )


def _received_at(raw: str | None):
    """Email dates are RFC 2822 — 'Mon, 27 Jul 2026 08:08:39 +0000'.

    Passing that string straight to a datetime field fails validation, which is what
    n8n will hand over too. Parsed here so the shape matches what production sends.
    """
    # timezone.utc rather than datetime.UTC: the alias only exists from 3.11, and a
    # helper script should not be the thing that fails on an older interpreter.
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    if raw:
        try:
            return parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)  # noqa: UP017


def _spreadsheet(filename: str, payload: bytes) -> Attachment:
    """Convert a workbook to rows here, mirroring what n8n will send."""
    from app.brain.tables import read_spreadsheet

    grids = read_spreadsheet(payload, filename)
    rows = grids[0].rows if grids else None
    return Attachment(filename=filename, kind="table" if rows else "other", rows=rows)


def _address(raw: str | None) -> str:
    import re

    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw or "")
    return match.group(0).lower() if match else "unknown@example.test"


def explain_connection_failure(error: Exception, url: str) -> str:
    """Turn a forty-line httpx traceback into the one sentence that helps.

    `get_client` already checks the shape of the URL and the key carefully, and says
    exactly what is wrong when either is malformed. It cannot check the one thing that
    fails most often in practice — whether the machine can reach the internet at all —
    because that is only discovered on the first request, well after the client is built.
    """
    text = f"{type(error).__name__}: {error}"

    if "getaddrinfo" in text or "Name or service not known" in text:
        host = url.replace("https://", "").replace("http://", "").rstrip("/")
        return (
            f"Could not look up {host}.\n\n"
            "  That is DNS, not the project: the address could not be resolved at all.\n"
            "  Usually the machine is offline, on a VPN, or behind a captive portal.\n\n"
            f"  Check with:  ping {host}"
        )

    if "ConnectTimeout" in text or "ReadTimeout" in text:
        return (
            f"Reached {url} but it did not answer in time.\n"
            "  Supabase may be paused — free projects sleep after a week idle. Open the\n"
            "  project in the dashboard once and try again."
        )

    if "SSL" in text or "CERTIFICATE" in text:
        return (
            "The TLS handshake failed. A corporate proxy intercepting HTTPS is the usual\n"
            "  cause; a different network is the quickest way to confirm it."
        )

    return text


def wipe(client, tenant_id: str) -> None:
    """Clear a tenant's data. Order matters: children before parents."""
    for table in (
        "offer_changes", "offers", "imports", "usage_events", "emails", "counterparties",
    ):
        client.table(table).delete().eq("tenant_id", tenant_id).execute()
    print(f"wiped existing data for tenant {tenant_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="tenant UUID")
    parser.add_argument("--tenant-name", help="tenant name, looked up instead of the UUID")
    parser.add_argument(
        "--samples", default=["samples"], nargs="+",
        help="one or more folders of .eml files. Several because the demo set and the "
             "real samples live apart, and a board built from only one of them is not "
             "the board anyone will see.",
    )
    parser.add_argument("--limit", type=int, help="only load the first N emails")
    parser.add_argument("--wipe", action="store_true", help="clear the tenant first")
    args = parser.parse_args()

    if not args.tenant and not args.tenant_name:
        parser.error("give either --tenant or --tenant-name")

    client = get_client()
    repository = SupabaseRepository(client)

    # Everything below here touches the network. Wrapped as one block because the reader
    # does not care which of these requests failed — they care that the database is
    # unreachable, and why.
    try:
        tenant_id = args.tenant
        if not tenant_id:
            rows = (
                client.table("tenants").select("id,name").eq("name", args.tenant_name).execute()
            ).data
            if not rows:
                print(f"no tenant named {args.tenant_name!r}. Run supabase/setup_tenant.sql first.")
                return 1
            tenant_id = rows[0]["id"]

        print(f"tenant {tenant_id}")

        if args.wipe:
            wipe(client, tenant_id)
    except Exception as error:
        print(f"\n{explain_connection_failure(error, get_settings().supabase_url)}")
        return 1

    paths = sorted(
        path for folder in args.samples for path in Path(folder).rglob("*.eml")
    )
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print(f"no .eml files under {', '.join(args.samples)}")
        return 1

    outcomes: Counter[str] = Counter()
    failures: list[tuple[str, str]] = []
    rows_total = 0
    model_calls = 0
    model_cost = 0.0

    for path in paths:
        try:
            payload = read_email(path, tenant_id)
            result = process_email(payload, repository)
        except Exception as error:  # one bad email must not stop the rest
            outcomes["failed"] += 1
            detail = f"{type(error).__name__}: {error}"
            failures.append((path.name, detail))
            print(f"  {path.name[:46]:48} FAILED {detail[:110]}")
            continue

        outcomes[result.outcome] += 1
        rows_total += result.row_count
        model_calls += result.model_calls
        model_cost += result.model_cost_usd

        counts = ""
        if result.reconcile:
            counts = (
                f"+{result.reconcile.inserted} ~{result.reconcile.updated} "
                f"={result.reconcile.refreshed} -{result.reconcile.closed}"
            )
        print(f"  {path.name[:46]:48} {result.outcome:20} {result.row_count:5} rows  {counts}")

    print(f"\n{'=' * 72}")
    for outcome, count in outcomes.most_common():
        print(f"  {outcome:22} {count}")
    print(f"  {'rows extracted':22} {rows_total}")

    if model_calls:
        # Worth reading as a per-email figure: this is 45 emails, and the client's
        # inbox is 20-30 a day. Most emails should cost nothing at all.
        print(f"  {'model calls':22} {model_calls}  (${model_cost:.4f} over {len(paths)} emails)")
    else:
        print(f"  {'model calls':22} 0  (no ANTHROPIC_API_KEY, or the rules settled everything)")

    if failures:
        # Grouped, because the same underlying cause usually accounts for all of them
        # and scrolling back through 45 lines to notice that is a waste of a minute.
        print(f"\n{'-' * 72}\nFAILURES, grouped by cause\n{'-' * 72}")
        grouped: dict[str, list[str]] = {}
        for name, detail in failures:
            grouped.setdefault(detail[:180], []).append(name)

        for detail, names in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            print(f"\n  {len(names)} email(s):")
            print(f"    {detail}")
            for name in names[:4]:
                print(f"      - {name[:60]}")
            if len(names) > 4:
                print(f"      … and {len(names) - 4} more")

    live = (
        client.table("offers").select("id", count="exact")
        .eq("tenant_id", tenant_id).eq("status", "live").execute()
    ).count
    print(f"  {'live offers on board':22} {live}")
    print("\nRefresh the dashboard — it should update by itself if realtime is on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
