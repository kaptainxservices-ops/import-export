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
from app.db.models import CounterpartyConfig, EmailRecord, ImportRecord, TenantConfig

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
                    "notes": record.notes,
                }
            )
            .execute()
        ).data[0]
        return row["id"]


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


def _chunked(items: list[dict], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]
