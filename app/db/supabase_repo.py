"""The Repository backed by Supabase.

Connects with the secret key, which bypasses row-level security — necessary, because
the backend writes on behalf of every tenant. Every query here therefore filters by
`tenant_id` explicitly. RLS is the safety net for the dashboard; on this side of the
wall the filtering is ours to get right.

The logic this implements is all decided elsewhere. This file only reads and writes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.brain.reconcile import Action, ExistingOffer, IncomingOffer
from app.config import get_settings
from app.db.models import (
    Allocation,
    BoardOffer,
    CounterpartyConfig,
    Deal,
    EmailRecord,
    ImportRecord,
    ReviewItem,
    StoredEmail,
    SupplierSummary,
    TenantConfig,
    UsageEvent,
)

log = logging.getLogger(__name__)


def get_client() -> Any:
    """Build the Supabase client. Imported lazily so tests never need the package.

    The configuration is checked here rather than left to the client library, whose
    only complaint is 'Invalid URL' with no indication of what it received. Every
    message below names the actual value, because the mistakes are all invisible ones:
    the whole line pasted into the value, a trailing '/rest/v1/', a stale key name.
    """
    from supabase import create_client

    settings = get_settings()

    url = (settings.supabase_url or "").strip().rstrip("/")
    key = (settings.supabase_secret_key or "").strip()

    if not url:
        raise RuntimeError(
            "SUPABASE_URL is not set. Add it to .env as the bare project URL, "
            "e.g. SUPABASE_URL=https://abcdefgh.supabase.co"
        )
    if not key:
        raise RuntimeError(
            "SUPABASE_SECRET_KEY is not set. Supabase > Project Settings > API > "
            "Secret keys. Note the name changed: it used to be SUPABASE_SERVICE_ROLE_KEY."
        )

    if not url.startswith(("http://", "https://")):
        raise RuntimeError(f"SUPABASE_URL must start with https:// — got {url!r}")

    if "=" in url:
        raise RuntimeError(
            f"SUPABASE_URL contains an '=' — the whole line was probably pasted into "
            f"the value. Got {url!r}"
        )

    # The client library appends /rest/v1 itself; leaving it on produces requests to
    # /rest/v1/rest/v1/ that 404 with no useful message.
    if "/rest/v1" in url or "/auth/v1" in url:
        raise RuntimeError(
            f"SUPABASE_URL should be the base project URL with no path — got {url!r}"
        )

    if not key.startswith(("sb_secret_", "eyJ")):
        raise RuntimeError(
            "SUPABASE_SECRET_KEY does not look like a Supabase key. Expected either a "
            "new-format 'sb_secret_...' key or a legacy service_role JWT beginning "
            "'eyJ'. Supabase > Project Settings > API > Secret keys."
        )

    if key.startswith("sb_publishable_") or key.startswith("sb_anon_"):
        raise RuntimeError(
            "SUPABASE_SECRET_KEY is a publishable key. The backend needs the secret "
            "key, which bypasses row-level security; the publishable one cannot write."
        )

    return create_client(url, key)


class SupabaseRepository:
    def __init__(self, client: Any | None = None) -> None:
        self.client = client or get_client()

    # ---------------------------------------------------------------- config

    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        rows = (
            self.client.table("tenants")
            .select("id,name,internal_domains,internal_addresses,staleness_hours")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        ).data
        if not rows:
            return None

        row = rows[0]
        return TenantConfig(
            id=row["id"],
            name=row.get("name") or "",
            internal_domains=set(row.get("internal_domains") or []),
            internal_addresses=set(row.get("internal_addresses") or []),
            staleness_hours=row.get("staleness_hours") or 24,
        )

    def find_counterparty(self, tenant_id: str, email: str) -> CounterpartyConfig | None:
        rows = (
            self.client.table("counterparties")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("primary_email", email.lower())
            .limit(1)
            .execute()
        ).data
        return _counterparty(rows[0]) if rows else None

    def get_counterparty(self, tenant_id: str, counterparty_id: str) -> CounterpartyConfig | None:
        rows = (
            self.client.table("counterparties")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("id", counterparty_id)
            .limit(1)
            .execute()
        ).data
        return _counterparty(rows[0]) if rows else None

    def create_counterparty(
        self, tenant_id: str, email: str, name: str | None
    ) -> CounterpartyConfig:
        row = (
            self.client.table("counterparties")
            .insert({"tenant_id": tenant_id, "primary_email": email.lower(), "name": name})
            .execute()
        ).data[0]
        log.info("created counterparty tenant=%s email=%s", tenant_id, email)
        return _counterparty(row)

    # ---------------------------------------------------------------- emails

    def email_already_seen(self, tenant_id: str, message_id: str) -> bool:
        rows = (
            self.client.table("emails")
            .select("id")
            .eq("tenant_id", tenant_id)
            .eq("message_id", message_id)
            .limit(1)
            .execute()
        ).data
        return bool(rows)

    def save_email(self, record: EmailRecord) -> str:
        row = (
            self.client.table("emails")
            .insert(
                {
                    "tenant_id": record.tenant_id,
                    "counterparty_id": record.counterparty_id,
                    "message_id": record.message_id,
                    "from_email": record.from_email,
                    "subject": record.subject,
                    "received_at": record.received_at.isoformat(),
                    "body_text": record.body_text,
                    "body_raw": record.body_raw,
                    "classification": _classification(record.classification),
                    "counterparty_method": record.counterparty_method,
                    "counterparty_confidence": record.counterparty_confidence,
                    "needs_sender_review": record.needs_sender_review,
                    "suggested_sender_name": record.suggested_sender_name,
                    "attachments": record.attachments,
                }
            )
            .execute()
        ).data[0]
        return row["id"]

    # ---------------------------------------------------------------- offers

    def load_live_offers(
        self, tenant_id: str, counterparty_id: str, side: str
    ) -> list[ExistingOffer]:
        rows = (
            self.client.table("offers")
            .select("id,identity_key,quantity,unit_price,currency,status,last_confirmed_at")
            .eq("tenant_id", tenant_id)
            .eq("counterparty_id", counterparty_id)
            .eq("side", side)
            .eq("status", "live")
            .execute()
        ).data

        return [
            ExistingOffer(
                id=row["id"],
                identity_key=row["identity_key"],
                quantity=row.get("quantity"),
                unit_price=float(row["unit_price"]) if row.get("unit_price") is not None else None,
                currency=row.get("currency"),
                status=row.get("status", "live"),
            )
            for row in rows
        ]

    def previous_row_count(self, tenant_id: str, counterparty_id: str) -> int | None:
        rows = (
            self.client.table("imports")
            .select("row_count")
            .eq("tenant_id", tenant_id)
            .eq("counterparty_id", counterparty_id)
            .eq("status", "applied")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data
        return rows[0]["row_count"] if rows else None

    def apply_actions(
        self,
        tenant_id: str,
        counterparty_id: str,
        email_id: str | None,
        side: str,
        actions: list[Action],
        incoming_by_key: dict[str, IncomingOffer],
    ) -> None:
        """Write the decisions out.

        Grouped by kind so each becomes a small number of round trips rather than one
        per row — a 2,000-row list would otherwise be 2,000 requests.
        """
        inserts: list[dict] = []
        changes: list[dict] = []

        # An ISO timestamp, not the string "now()". PostgREST sends JSON values as
        # literals, so "now()" would be stored as those six characters and every
        # timestamp comparison against it would fail — silently, since the column is
        # text-parseable and the insert succeeds.
        # timezone.utc rather than datetime.UTC: the alias only exists from 3.11, and
        # this should not be the file that breaks on an older interpreter.
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        for action in actions:
            incoming = action.incoming or incoming_by_key.get(action.identity_key)

            if action.kind == "insert" and incoming is not None:
                inserts.append(
                    {
                        "tenant_id": tenant_id,
                        "counterparty_id": counterparty_id,
                        "source_email_id": email_id,
                        "side": side,
                        "description": incoming.description,
                        "quantity": incoming.quantity,
                        "unit_price": incoming.unit_price,
                        "currency": incoming.currency,
                        "source_ref": incoming.source_ref,
                        # The database recomputes identity_key from these as a stored
                        # generated column. Omit them and every row keys as 'spec:|||',
                        # so the second insert violates the uniqueness constraint.
                        **{k: v for k, v in incoming.fields.items() if v is not None},
                    }
                )

            elif action.kind == "refresh" and action.offer_id:
                self.client.table("offers").update({"last_confirmed_at": now}).eq(
                    "id", action.offer_id
                ).execute()

            elif action.kind == "update" and action.offer_id and incoming is not None:
                self.client.table("offers").update(
                    {
                        "quantity": incoming.quantity,
                        "unit_price": incoming.unit_price,
                        "currency": incoming.currency,
                        "last_confirmed_at": now,
                    }
                ).eq("id", action.offer_id).execute()

                changes.extend(
                    {
                        "tenant_id": tenant_id,
                        "offer_id": action.offer_id,
                        "field": change.field,
                        "old_value": change.old,
                        "new_value": change.new,
                        "cause": "daily_list",
                        "caused_by_email_id": email_id,
                    }
                    for change in action.changes
                )

            elif action.kind == "close" and action.offer_id:
                self.client.table("offers").update(
                    {"status": "sold", "close_reason": action.reason, "closed_at": now}
                ).eq("id", action.offer_id).execute()

                changes.append(
                    {
                        "tenant_id": tenant_id,
                        "offer_id": action.offer_id,
                        "field": "status",
                        "old_value": "live",
                        "new_value": "sold",
                        "cause": "daily_list",
                        "caused_by_email_id": email_id,
                    }
                )

            elif action.kind == "reopen" and action.offer_id and incoming is not None:
                self.client.table("offers").update(
                    {
                        "status": "live",
                        "close_reason": None,
                        "closed_at": None,
                        "quantity": incoming.quantity,
                        "unit_price": incoming.unit_price,
                        "last_confirmed_at": now,
                    }
                ).eq("id", action.offer_id).execute()

        for batch in _chunked(inserts, 500):
            self.client.table("offers").insert(batch).execute()

        for batch in _chunked(changes, 500):
            self.client.table("offer_changes").insert(batch).execute()

    def record_import(self, record: ImportRecord) -> str:
        row = (
            self.client.table("imports")
            .insert(
                {
                    "tenant_id": record.tenant_id,
                    "counterparty_id": record.counterparty_id,
                    "email_id": record.email_id,
                    "row_count": record.row_count,
                    "previous_row_count": record.previous_row_count,
                    "is_complete_list": record.is_complete_list,
                    "status": record.status,
                    "offers_inserted": record.offers_inserted,
                    "offers_updated": record.offers_updated,
                    "offers_refreshed": record.offers_refreshed,
                    "offers_closed": record.offers_closed,
                    "rows_expanded_from_variants": record.rows_expanded_from_variants,
                    "duplicate_rows": record.duplicate_rows,
                    "flagged_rows": record.flagged_rows,
                    "parse_warnings": record.parse_warnings,
                    "notes": record.notes,
                }
            )
            .execute()
        ).data[0]
        return row["id"]

    def record_usage(self, events: list[UsageEvent]) -> None:
        """Write the model spend. Swallows its own failures on purpose.

        This table is accounting: nothing reads it to make a decision. Letting a failed
        insert here propagate would throw away an email that has already been parsed,
        reconciled and written — losing real work to protect a cost report.
        """
        if not events:
            return

        try:
            self.client.table("usage_events").insert(
                [
                    {
                        "tenant_id": event.tenant_id,
                        "email_id": event.email_id,
                        "operation": event.operation,
                        "model": event.model,
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                        "cost_usd": event.cost_usd,
                        "duration_ms": event.duration_ms,
                        "succeeded": event.succeeded,
                        "error": event.error,
                    }
                    for event in events
                ]
            ).execute()
        except Exception as error:  # noqa: BLE001 — accounting must not cost us an email
            log.warning("could not record usage: %s", error)

    # ---------------------------------------------------------------- review

    _REVIEW_COLUMNS = (
        "id,received_at,from_email,subject,classification,needs_sender_review,"
        "counterparty_id,counterparty_method,counterparty_confidence,"
        "suggested_sender_name,body_text,attachments,counterparties(name)"
    )

    def list_review_queue(self, tenant_id: str, limit: int = 200) -> list[ReviewItem]:
        rows = (
            self.client.table("emails")
            .select(self._REVIEW_COLUMNS)
            .eq("tenant_id", tenant_id)
            # PostgREST's `or` takes one string. Either the sender could not be
            # established or the side could not be decided; both mean nothing was
            # reconciled and somebody has to look.
            .or_("needs_sender_review.eq.true,classification.eq.unclassified")
            .order("received_at", desc=True)
            .limit(limit)
            .execute()
        ).data

        return [_review_item(row) for row in rows]

    # ---------------------------------------------------------------- imports

    _IMPORT_COLUMNS = (
        "id,created_at,row_count,flagged_rows,parse_warnings,status,is_complete_list,"
        "previous_row_count,offers_inserted,offers_updated,offers_closed,notes,"
        "counterparty_id,email_id,counterparties(name,primary_email),"
        "emails(subject,received_at,attachments)"
    )

    def list_pending_imports(self, tenant_id: str, limit: int = 50) -> list[dict]:
        """Imports nobody has approved or rejected yet, newest first."""
        return (
            self.client.table("imports")
            .select(self._IMPORT_COLUMNS)
            .eq("tenant_id", tenant_id)
            .is_("approved_at", "null")
            .is_("rejected_at", "null")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data

    def approve_import(self, tenant_id: str, import_id: str, user_id: str | None) -> bool:
        """Accept the import as read.

        Deliberately does *not* clear `needs_review` on the flagged rows. "Approve 475"
        and "review the 12" are two actions, and collapsing them would let one click
        push twelve doubtful rows onto the board — which is the exact outcome the queue
        exists to prevent.
        """
        rows = (
            self.client.table("imports")
            .update(
                {
                    "approved_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                    "reviewed_by": user_id,
                }
            )
            .eq("tenant_id", tenant_id)
            .eq("id", import_id)
            .execute()
        ).data
        return bool(rows)

    def reject_import(self, tenant_id: str, import_id: str, user_id: str | None) -> dict:
        """Undo an import wholesale, and put back what it displaced.

        This is the action for the case the spec describes: 340 flagged of 392, because
        the sender changed their template and the columns misaligned. Correcting 340 rows
        by hand is not a plan.

        Two halves, and the second is the one that is easy to forget. Withdrawing the bad
        rows is obvious. But this import also *closed* the supplier's previous list as
        sold, and leaving that closed would take the whole supplier off the board — the
        bad parse would have destroyed real stock even after being rejected. The change
        log records every closure with the email that caused it, which is what makes the
        reopening possible; it is the reason that log exists.
        """
        record = (
            self.client.table("imports")
            .select("email_id")
            .eq("tenant_id", tenant_id)
            .eq("id", import_id)
            .limit(1)
            .execute()
        ).data
        if not record:
            return {"rejected": False}

        email_id = record[0].get("email_id")
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        withdrawn = len(
            (
                self.client.table("offers")
                .update(
                    {
                        "status": "withdrawn",
                        "close_reason": "import rejected",
                        "closed_at": now,
                    }
                )
                .eq("tenant_id", tenant_id)
                .eq("source_email_id", email_id)
                .execute()
            ).data
            or []
        )

        reopened = 0
        if email_id:
            closed_by_this = (
                self.client.table("offer_changes")
                .select("offer_id")
                .eq("tenant_id", tenant_id)
                .eq("caused_by_email_id", email_id)
                .eq("field", "status")
                .eq("new_value", "sold")
                .execute()
            ).data or []

            ids = [row["offer_id"] for row in closed_by_this if row.get("offer_id")]
            for batch in _chunked([{"id": i} for i in ids], 200):
                reopened += len(
                    (
                        self.client.table("offers")
                        .update({"status": "live", "close_reason": None, "closed_at": None})
                        .eq("tenant_id", tenant_id)
                        .in_("id", [row["id"] for row in batch])
                        .execute()
                    ).data
                    or []
                )

        self.client.table("imports").update(
            {"rejected_at": now, "reviewed_by": user_id, "status": "failed"}
        ).eq("tenant_id", tenant_id).eq("id", import_id).execute()

        log.info(
            "import %s rejected on tenant %s: %d withdrawn, %d reopened",
            import_id, tenant_id, withdrawn, reopened,
        )
        return {"rejected": True, "withdrawn": withdrawn, "reopened": reopened}

    def list_flagged_rows(self, tenant_id: str, import_id: str | None = None) -> list[dict]:
        query = (
            self.client.table("offers")
            .select(
                "id,description,quantity,unit_price,currency,confidence,review_reason,"
                "confidence_json,source_ref,source_email_id,side,counterparties(name)"
            )
            .eq("tenant_id", tenant_id)
            .eq("needs_review", True)
            .order("created_at", desc=True)
            .limit(300)
        )
        if import_id:
            email = (
                self.client.table("imports")
                .select("email_id")
                .eq("tenant_id", tenant_id)
                .eq("id", import_id)
                .limit(1)
                .execute()
            ).data
            if email and email[0].get("email_id"):
                query = query.eq("source_email_id", email[0]["email_id"])

        return query.execute().data

    def resolve_row(self, tenant_id: str, offer_id: str, changes: dict) -> bool:
        """Apply a human's correction and let the row onto the board."""
        allowed = {
            k: v for k, v in changes.items()
            if k in {"description", "quantity", "unit_price", "currency"}
        }
        allowed.update({"needs_review": False, "review_reason": None, "confidence": 1.0})

        rows = (
            self.client.table("offers")
            .update(allowed)
            .eq("tenant_id", tenant_id)
            .eq("id", offer_id)
            .execute()
        ).data
        return bool(rows)

    # ---------------------------------------------------------------- deals

    _DEAL_COLUMNS = (
        "id,tenant_id,reference,title,status,status_updated_at,owner_id,"
        "keep_offers_visible,opened_at,closed_at,outcome,quoted_unit_price,loss_reason,"
        "deal_allocations(id,deal_id,side,counterparty_id,offer_id,quantity,unit_price,"
        "currency,note,counterparties(name),offers(description))"
    )

    def list_deals(self, tenant_id: str, include_closed: bool = False) -> list[Deal]:
        query = (
            self.client.table("deals")
            .select(self._DEAL_COLUMNS)
            .eq("tenant_id", tenant_id)
            .order("opened_at", desc=True)
            .limit(500)
        )
        if not include_closed:
            query = query.is_("closed_at", "null")

        return [_deal(row) for row in query.execute().data]

    def get_deal(self, tenant_id: str, deal_id: str) -> Deal | None:
        rows = (
            self.client.table("deals")
            .select(self._DEAL_COLUMNS)
            .eq("tenant_id", tenant_id)
            .eq("id", deal_id)
            .limit(1)
            .execute()
        ).data
        return _deal(rows[0]) if rows else None

    def create_deal(self, tenant_id: str, title: str | None, status: str) -> Deal:
        # The reference is allocated by the database rather than counted here: two
        # traders opening a deal in the same second would otherwise both get #115.
        reference = self.client.rpc(
            "next_deal_reference", {"p_tenant": tenant_id}
        ).execute().data

        row = (
            self.client.table("deals")
            .insert(
                {
                    "tenant_id": tenant_id,
                    "reference": reference,
                    "title": title,
                    "status": status,
                }
            )
            .execute()
        ).data[0]

        log.info("deal #%s opened on tenant %s", reference, tenant_id)
        return _deal(row)

    _DEAL_SETTABLE = frozenset(
        {"title", "status", "keep_offers_visible", "owner_id", "outcome",
         "quoted_unit_price", "loss_reason", "closed_at", "status_updated_at"}
    )

    def update_deal(self, tenant_id: str, deal_id: str, changes: dict) -> bool:
        allowed = {k: v for k, v in changes.items() if k in self._DEAL_SETTABLE}
        if not allowed:
            return False

        rows = (
            self.client.table("deals")
            .update(allowed)
            .eq("tenant_id", tenant_id)
            .eq("id", deal_id)
            .execute()
        ).data
        return bool(rows)

    def commitments_on_offer(self, tenant_id: str, offer_id: str) -> list[tuple]:
        rows = (
            self.client.table("deal_allocations")
            .select("deal_id,quantity,deals(reference,closed_at)")
            .eq("tenant_id", tenant_id)
            .eq("offer_id", offer_id)
            .execute()
        ).data

        out = []
        for row in rows:
            deal = row.get("deals") or {}
            out.append(
                (
                    row["deal_id"],
                    deal.get("reference") or 0,
                    row.get("quantity") or 0,
                    deal.get("closed_at") is None,
                )
            )
        return out

    def add_allocation(self, tenant_id: str, deal_id: str, leg: dict) -> Allocation:
        row = (
            self.client.table("deal_allocations")
            .insert({**leg, "tenant_id": tenant_id, "deal_id": deal_id})
            .execute()
        ).data[0]
        return _allocation(row)

    def remove_allocation(self, tenant_id: str, allocation_id: str) -> bool:
        rows = (
            self.client.table("deal_allocations")
            .delete()
            .eq("tenant_id", tenant_id)
            .eq("id", allocation_id)
            .execute()
        ).data
        return bool(rows)

    # ---------------------------------------------------------------- suppliers

    # Only these. A whitelist rather than "whatever the browser sent" because this is a
    # PATCH straight into a table: without it, `name` would be editable and so would
    # `tenant_id`, and the second one moves a supplier to another client's board.
    _SUPPLIER_SETTABLE = frozenset(
        {"default_currency", "decimal_separator", "staleness_hours", "typical_row_count", "name"}
    )

    def list_suppliers(self, tenant_id: str) -> list[SupplierSummary]:
        rows = (
            self.client.table("counterparties")
            .select("*")
            .eq("tenant_id", tenant_id)
            .order("name")
            .limit(1000)
            .execute()
        ).data

        summaries = []
        for row in rows:
            offers = (
                self.client.table("offers")
                .select("unit_price,currency,last_confirmed_at", count="exact")
                .eq("tenant_id", tenant_id)
                .eq("counterparty_id", row["id"])
                .eq("status", "live")
                .order("last_confirmed_at", desc=True)
                .limit(6)
                .execute()
            )

            recent = offers.data or []
            summaries.append(
                SupplierSummary(
                    id=row["id"],
                    primary_email=row.get("primary_email") or "",
                    name=row.get("name"),
                    country=row.get("country"),
                    default_currency=row.get("default_currency"),
                    decimal_separator=row.get("decimal_separator"),
                    staleness_hours=row.get("staleness_hours"),
                    typical_row_count=row.get("typical_row_count"),
                    live_offers=offers.count or 0,
                    last_seen_at=(
                        _timestamp(recent[0].get("last_confirmed_at")) if recent else None
                    ),
                    # The evidence for the settings above. Nobody knows offhand whether a
                    # supplier writes '1,079' meaning a thousand; seeing five of their
                    # prices makes it obvious.
                    sample_prices=[
                        f"{o['unit_price']} {o.get('currency') or ''}".strip()
                        for o in recent[:5]
                        if o.get("unit_price") is not None
                    ],
                )
            )

        return summaries

    def update_supplier(self, tenant_id: str, supplier_id: str, changes: dict) -> bool:
        allowed = {k: v for k, v in changes.items() if k in self._SUPPLIER_SETTABLE}
        if not allowed:
            return False

        rows = (
            self.client.table("counterparties")
            .update(allowed)
            .eq("tenant_id", tenant_id)
            .eq("id", supplier_id)
            .execute()
        ).data
        return bool(rows)

    def count_emails(self, tenant_id: str) -> int:
        return (
            self.client.table("emails")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            # head=True asks for the count without the rows. Fetching 5,000 bodies to
            # find out whether the number is zero would be an expensive way to render
            # one sentence.
            .limit(1)
            .execute()
        ).count or 0

    def get_stored_email(self, tenant_id: str, email_id: str) -> StoredEmail | None:
        rows = (
            self.client.table("emails")
            .select(
                "id,tenant_id,message_id,from_email,subject,received_at,"
                "body_text,body_raw,attachments,counterparty_id,classification"
            )
            .eq("tenant_id", tenant_id)
            .eq("id", email_id)
            .limit(1)
            .execute()
        ).data
        if not rows:
            return None

        row = rows[0]
        return StoredEmail(
            id=row["id"],
            tenant_id=row["tenant_id"],
            message_id=row.get("message_id") or "",
            from_email=row.get("from_email") or "",
            subject=row.get("subject") or "",
            received_at=_timestamp(row.get("received_at")),
            body_text=row.get("body_text") or "",
            body_raw=row.get("body_raw") or "",
            attachments=row.get("attachments") or [],
            counterparty_id=row.get("counterparty_id"),
            classification=_side(row.get("classification")),
        )

    def attribute_email(
        self,
        tenant_id: str,
        email_id: str,
        counterparty_id: str | None = None,
        classification: str | None = None,
    ) -> bool:
        changes: dict[str, Any] = {}

        if counterparty_id is not None:
            changes["counterparty_id"] = counterparty_id
            # 'manual' is a real value in public.sender_method, and it matters that the
            # method says so: the next person to look at this row should be able to tell
            # a resolved address from a guessed one.
            changes["counterparty_method"] = "manual"
            changes["counterparty_confidence"] = 1.0
            changes["needs_sender_review"] = False

        if classification is not None:
            changes["classification"] = _classification(classification)

        if not changes:
            return False

        # The tenant filter is the security boundary, not a nicety: without it an email
        # id from one client's queue would resolve an email on another client's board.
        rows = (
            self.client.table("emails")
            .update(changes)
            .eq("tenant_id", tenant_id)
            .eq("id", email_id)
            .execute()
        ).data
        return bool(rows)

    # ---------------------------------------------------------------- matching

    def tenant_for_user(self, user_id: str) -> str | None:
        rows = (
            self.client.table("profiles")
            .select("tenant_id,role")
            .eq("id", user_id)
            .limit(1)
            .execute()
        ).data
        if not rows:
            return None

        row = rows[0]
        if row.get("tenant_id"):
            return row["tenant_id"]

        # The platform owner has no tenant of their own. Give them the first one so the
        # board is usable; a tenant switcher belongs in the admin panel, not here.
        if row.get("role") == "platform_owner":
            tenants = (
                self.client.table("tenants").select("id").order("created_at").limit(1).execute()
            ).data
            return tenants[0]["id"] if tenants else None

        return None

    def get_board_offer(self, tenant_id: str, offer_id: str) -> BoardOffer | None:
        rows = (
            self.client.table("offers")
            .select(_BOARD_COLUMNS)
            .eq("tenant_id", tenant_id)
            .eq("id", offer_id)
            .limit(1)
            .execute()
        ).data
        return _board_offer(rows[0]) if rows else None

    def load_board(self, tenant_id: str, side: str, limit: int = 5000) -> list[BoardOffer]:
        rows = (
            self.client.table("offers")
            .select(_BOARD_COLUMNS)
            .eq("tenant_id", tenant_id)
            .eq("side", side)
            .eq("status", "live")
            .limit(limit)
            .execute()
        ).data
        return [_board_offer(row) for row in rows]


_BOARD_COLUMNS = (
    "id,counterparty_id,side,description,quantity,unit_price,currency,"
    "identity_key,description_key,ean,brand,category,capacity_gb,colour,"
    "counterparties(name,country)"
)


def _board_offer(row: dict) -> BoardOffer:
    counterparty = row.get("counterparties") or {}
    return BoardOffer(
        id=row["id"],
        counterparty_id=row["counterparty_id"],
        counterparty_name=counterparty.get("name"),
        side=row["side"],
        description=row.get("description") or "",
        quantity=row.get("quantity"),
        unit_price=float(row["unit_price"]) if row.get("unit_price") is not None else None,
        currency=row.get("currency"),
        identity_key=row.get("identity_key") or "",
        # match_key is not stored: it is derived, and derived values in the database go
        # stale the moment the derivation changes. Recomputed on read instead.
        match_key=None,
        description_key=row.get("description_key"),
        ean=row.get("ean"),
        brand=row.get("brand"),
        category=row.get("category"),
        capacity_gb=row.get("capacity_gb"),
        colour=row.get("colour"),
        country=counterparty.get("country"),
    )


def _allocation(row: dict) -> Allocation:
    counterparty = row.get("counterparties") or {}
    offer = row.get("offers") or {}
    return Allocation(
        id=row["id"],
        deal_id=row["deal_id"],
        side=row["side"],
        counterparty_id=row["counterparty_id"],
        counterparty_name=counterparty.get("name"),
        offer_id=row.get("offer_id"),
        # The offer may since have been superseded, so the description is kept as a
        # fallback rather than depended on. A deal must outlive its source list.
        description=offer.get("description") or "",
        quantity=row.get("quantity") or 0,
        unit_price=float(row["unit_price"]) if row.get("unit_price") is not None else None,
        currency=row.get("currency"),
        note=row.get("note"),
    )


def _deal(row: dict) -> Deal:
    return Deal(
        id=row["id"],
        tenant_id=row["tenant_id"],
        reference=row.get("reference") or 0,
        title=row.get("title"),
        status=row.get("status") or "",
        status_updated_at=_timestamp(row.get("status_updated_at")),
        owner_id=row.get("owner_id"),
        keep_offers_visible=bool(row.get("keep_offers_visible", True)),
        opened_at=_timestamp(row.get("opened_at")),
        closed_at=_timestamp(row["closed_at"]) if row.get("closed_at") else None,
        outcome=row.get("outcome"),
        quoted_unit_price=(
            float(row["quoted_unit_price"]) if row.get("quoted_unit_price") is not None else None
        ),
        loss_reason=row.get("loss_reason"),
        allocations=[_allocation(a) for a in (row.get("deal_allocations") or [])],
    )


def _counterparty(row: dict) -> CounterpartyConfig:
    return CounterpartyConfig(
        id=row["id"],
        primary_email=row["primary_email"],
        name=row.get("name"),
        default_currency=row.get("default_currency"),
        decimal_separator=row.get("decimal_separator"),
        typical_row_count=row.get("typical_row_count"),
        staleness_hours=row.get("staleness_hours"),
    )


def _classification(side: str) -> str:
    return {"sell": "seller_offer", "buy": "buyer_request"}.get(side, "unclassified")


def _side(classification: str | None) -> str:
    """The inverse of _classification. The database stores the trade's word for it and
    the brain uses the short one; going one way and not the other left `reprocess_email`
    unable to recognise the side it had just been given."""
    return {"seller_offer": "sell", "buyer_request": "buy"}.get(
        classification or "", "unclassified"
    )


def _timestamp(raw: str | None) -> datetime:
    """PostgREST returns ISO 8601 with a 'Z' that fromisoformat rejects before 3.11."""
    if not raw:
        return datetime.now(timezone.utc)  # noqa: UP017
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)  # noqa: UP017


def _review_item(row: dict) -> ReviewItem:
    counterparty = row.get("counterparties") or {}
    body = row.get("body_text") or ""
    return ReviewItem(
        id=row["id"],
        received_at=_timestamp(row.get("received_at")),
        from_email=row.get("from_email") or "",
        subject=row.get("subject") or "",
        classification=_side(row.get("classification")),
        needs_sender_review=bool(row.get("needs_sender_review")),
        counterparty_id=row.get("counterparty_id"),
        counterparty_name=counterparty.get("name"),
        counterparty_method=row.get("counterparty_method") or "envelope",
        counterparty_confidence=float(row.get("counterparty_confidence") or 0),
        suggested_sender_name=row.get("suggested_sender_name"),
        # Enough to recognise the email without shipping a 300kb body to the browser for
        # every row in the queue.
        body_preview=body[:400],
        attachment_count=len(row.get("attachments") or []),
    )


def _chunked(items: list[dict], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]
