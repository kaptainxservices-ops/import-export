"""An in-memory Repository, used by the tests.

This is not a mock that records calls and asserts on them. It is a working store that
holds offers, applies reconciliation actions, keeps a change log and enforces the same
identity rule as the database. That means a pipeline test proves the offers really do
end up in the right state — which is the only claim worth making about reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from itertools import count

from app.brain.reconcile import Action, ExistingOffer, IncomingOffer
from app.db.models import (
    CounterpartyConfig,
    EmailRecord,
    ImportRecord,
    ReviewItem,
    StoredEmail,
    SupplierSummary,
    TenantConfig,
    UsageEvent,
)


@dataclass
class StoredOffer:
    id: str
    tenant_id: str
    counterparty_id: str
    side: str
    identity_key: str
    description: str = ""
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    status: str = "live"
    close_reason: str | None = None
    needs_review: bool = False
    # Which email put this row here. The real table has it, and without it there is no
    # way to undo one import -- rejecting a bad parse is 'withdraw what this email
    # added', and nothing else identifies that set.
    source_email_id: str | None = None
    source_ref: str | None = None
    confidence: float | None = None
    review_reason: str | None = None
    first_seen_at: datetime = field(default_factory=datetime.utcnow)
    last_confirmed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class StoredImport:
    """An import with the two things ImportRecord has no room for: an id, and whether
    a person has passed judgement on it yet."""

    id: str
    record: ImportRecord
    created_at: datetime = field(default_factory=datetime.utcnow)
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    reviewed_by: str | None = None


@dataclass
class ChangeLogEntry:
    offer_id: str
    field: str
    old: str | None
    new: str | None
    cause: str
    email_id: str | None


class InMemoryRepository:
    def __init__(self, tenants: list[TenantConfig] | None = None) -> None:
        self.tenants = {t.id: t for t in (tenants or [])}
        self.counterparties: dict[str, CounterpartyConfig] = {}
        self.emails: dict[str, EmailRecord] = {}
        self.seen_message_ids: set[tuple[str, str]] = set()
        self.offers: dict[str, StoredOffer] = {}
        self.changes: list[ChangeLogEntry] = []
        self.imports: list[StoredImport] = []
        self.usage: list[UsageEvent] = []
        self._ids = count(1)

    def _next(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids)}"

    # ---------------------------------------------------------------- config

    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        return self.tenants.get(tenant_id)

    def find_counterparty(self, tenant_id: str, email: str) -> CounterpartyConfig | None:
        for cp in self.counterparties.values():
            if cp.primary_email.lower() == email.lower():
                return cp
        return None

    def get_counterparty(self, tenant_id: str, counterparty_id: str) -> CounterpartyConfig | None:
        return self.counterparties.get(counterparty_id)

    def create_counterparty(
        self, tenant_id: str, email: str, name: str | None
    ) -> CounterpartyConfig:
        cp = CounterpartyConfig(id=self._next("cp"), primary_email=email, name=name)
        self.counterparties[cp.id] = cp
        return cp

    # ---------------------------------------------------------------- emails

    def email_already_seen(self, tenant_id: str, message_id: str) -> bool:
        return (tenant_id, message_id) in self.seen_message_ids

    def save_email(self, record: EmailRecord) -> str:
        email_id = self._next("email")
        self.emails[email_id] = record
        self.seen_message_ids.add((record.tenant_id, record.message_id))
        return email_id

    # ---------------------------------------------------------------- offers

    def load_live_offers(
        self, tenant_id: str, counterparty_id: str, side: str
    ) -> list[ExistingOffer]:
        return [
            ExistingOffer(
                id=o.id,
                identity_key=o.identity_key,
                quantity=o.quantity,
                unit_price=o.unit_price,
                currency=o.currency,
                status=o.status,
                last_confirmed_at=o.last_confirmed_at,
            )
            for o in self.offers.values()
            if o.tenant_id == tenant_id and o.counterparty_id == counterparty_id
            and o.side == side
        ]

    def previous_row_count(self, tenant_id: str, counterparty_id: str) -> int | None:
        for stored in reversed(self.imports):
            record = stored.record
            if (
                record.tenant_id == tenant_id
                and record.counterparty_id == counterparty_id
                and record.status == "applied"
            ):
                return record.row_count
        return None

    def apply_actions(
        self,
        tenant_id: str,
        counterparty_id: str,
        email_id: str | None,
        side: str,
        actions: list[Action],
        incoming_by_key: dict[str, IncomingOffer],
    ) -> None:
        now = datetime.utcnow()

        for action in actions:
            incoming = action.incoming or incoming_by_key.get(action.identity_key)

            if action.kind == "insert" and incoming is not None:
                offer_id = self._next("offer")
                self.offers[offer_id] = StoredOffer(
                    id=offer_id,
                    tenant_id=tenant_id,
                    counterparty_id=counterparty_id,
                    side=side,
                    identity_key=incoming.identity_key,
                    description=incoming.description,
                    quantity=incoming.quantity,
                    unit_price=incoming.unit_price,
                    currency=incoming.currency,
                    source_email_id=email_id,
                    source_ref=incoming.source_ref,
                    needs_review=bool(incoming.fields.get("needs_review")),
                    review_reason=incoming.fields.get("review_reason"),
                    confidence=incoming.fields.get("confidence"),
                    first_seen_at=now,
                    last_confirmed_at=now,
                )
                continue

            offer = self.offers.get(action.offer_id or "")
            if offer is None:
                continue

            if action.kind == "refresh":
                offer.last_confirmed_at = now

            elif action.kind == "update" and incoming is not None:
                for change in action.changes:
                    self.changes.append(
                        ChangeLogEntry(
                            offer_id=offer.id,
                            field=change.field,
                            old=change.old,
                            new=change.new,
                            cause="daily_list",
                            email_id=email_id,
                        )
                    )
                offer.quantity = incoming.quantity
                offer.unit_price = incoming.unit_price
                offer.currency = incoming.currency or offer.currency
                offer.last_confirmed_at = now
                offer.needs_review = action.needs_review

            elif action.kind == "close":
                self.changes.append(
                    ChangeLogEntry(
                        offer_id=offer.id,
                        field="status",
                        old=offer.status,
                        new="sold",
                        cause="daily_list",
                        email_id=email_id,
                    )
                )
                offer.status = "sold"
                offer.close_reason = action.reason

            elif action.kind == "reopen" and incoming is not None:
                offer.status = "live"
                offer.close_reason = None
                offer.quantity = incoming.quantity
                offer.unit_price = incoming.unit_price
                offer.last_confirmed_at = now

    def record_import(self, record: ImportRecord) -> str:
        # The id is generated before storing, not after. Returning an id that was never
        # attached to anything made every import unaddressable -- approve and reject had
        # nothing to act on.
        import_id = self._next("import")
        self.imports.append(StoredImport(id=import_id, record=record))
        return import_id

    def record_usage(self, events: list[UsageEvent]) -> None:
        self.usage.extend(events)

    # ---------------------------------------------------------------- review

    def list_review_queue(self, tenant_id: str, limit: int = 200) -> list[ReviewItem]:
        items = [
            ReviewItem(
                id=email_id,
                received_at=record.received_at,
                from_email=record.from_email,
                subject=record.subject,
                classification=record.classification,
                needs_sender_review=record.needs_sender_review,
                counterparty_id=record.counterparty_id,
                counterparty_name=(
                    self.counterparties[record.counterparty_id].name
                    if record.counterparty_id in self.counterparties
                    else None
                ),
                counterparty_method=record.counterparty_method,
                counterparty_confidence=record.counterparty_confidence,
                suggested_sender_name=record.suggested_sender_name,
                body_preview=(record.body_text or "")[:400],
                attachment_count=len(record.attachments),
            )
            for email_id, record in self.emails.items()
            if record.tenant_id == tenant_id
            and (record.needs_sender_review or record.classification == "unclassified")
        ]
        return sorted(items, key=lambda i: i.received_at, reverse=True)[:limit]

    _SUPPLIER_SETTABLE = frozenset(
        {"default_currency", "decimal_separator", "staleness_hours", "typical_row_count", "name"}
    )

    # ---------------------------------------------------------------- list review

    def list_pending_imports(self, tenant_id: str, limit: int = 50) -> list[dict]:
        """Shaped exactly like the Supabase join, so the API code is exercised the same.

        That matters more than it looks: the endpoint reads `counterparties(name)` and
        `emails(subject)` as nested objects, and a flat test double would let a real
        KeyError through every test.
        """
        out: list[dict] = []
        for stored in reversed(self.imports):
            record = stored.record
            if record.tenant_id != tenant_id:
                continue
            if stored.approved_at or stored.rejected_at:
                continue

            counterparty = self.counterparties.get(record.counterparty_id or "")
            email = self.emails.get(record.email_id or "")

            out.append(
                {
                    "id": stored.id,
                    "created_at": stored.created_at.isoformat(),
                    "row_count": record.row_count,
                    "flagged_rows": record.flagged_rows,
                    "parse_warnings": list(record.parse_warnings),
                    "status": record.status,
                    "is_complete_list": record.is_complete_list,
                    "previous_row_count": record.previous_row_count,
                    "offers_inserted": record.offers_inserted,
                    "offers_updated": record.offers_updated,
                    "offers_closed": record.offers_closed,
                    "notes": record.notes,
                    "counterparty_id": record.counterparty_id,
                    "email_id": record.email_id,
                    "counterparties": (
                        {"name": counterparty.name, "primary_email": counterparty.primary_email}
                        if counterparty
                        else None
                    ),
                    "emails": (
                        {
                            "subject": email.subject,
                            "received_at": email.received_at.isoformat(),
                            "attachments": email.attachments,
                        }
                        if email
                        else None
                    ),
                }
            )
            if len(out) >= limit:
                break
        return out

    def _find_import(self, tenant_id: str, import_id: str):
        for stored in self.imports:
            if stored.id == import_id and stored.record.tenant_id == tenant_id:
                return stored
        return None

    def approve_import(self, tenant_id: str, import_id: str, user_id: str | None) -> bool:
        stored = self._find_import(tenant_id, import_id)
        if stored is None:
            return False
        stored.approved_at = datetime.utcnow()
        stored.reviewed_by = user_id
        # Deliberately leaves needs_review alone on the flagged rows. Approving the list
        # is not approving the twelve rows nobody has looked at.
        return True

    def reject_import(self, tenant_id: str, import_id: str, user_id: str | None) -> dict:
        stored = self._find_import(tenant_id, import_id)
        if stored is None:
            return {"rejected": False}

        email_id = stored.record.email_id
        now = datetime.utcnow()

        withdrawn = 0
        for offer in self.offers.values():
            if offer.tenant_id == tenant_id and offer.source_email_id == email_id:
                if offer.status != "withdrawn":
                    offer.status = "withdrawn"
                    offer.close_reason = "import rejected"
                    withdrawn += 1

        # The half that is easy to forget. This import closed the supplier's previous
        # list as sold; leaving it closed means a rejected bad parse still destroyed
        # real stock. The change log is what makes putting it back possible.
        reopened = 0
        closed_by_this = {
            entry.offer_id
            for entry in self.changes
            if entry.field == "status" and entry.new == "sold" and entry.email_id == email_id
        }
        for offer_id in closed_by_this:
            offer = self.offers.get(offer_id)
            if offer is not None and offer.tenant_id == tenant_id and offer.status == "sold":
                offer.status = "live"
                offer.close_reason = None
                reopened += 1

        stored.rejected_at = now
        stored.reviewed_by = user_id
        # ImportRecord is frozen — replaced rather than mutated. Worth keeping frozen:
        # an import is a record of what happened, and a record that can be edited in
        # place is not an audit trail.
        stored.record = replace(stored.record, status="failed")

        return {"rejected": True, "withdrawn": withdrawn, "reopened": reopened}

    def list_flagged_rows(self, tenant_id: str, import_id: str | None = None) -> list[dict]:
        email_id = None
        if import_id:
            stored = self._find_import(tenant_id, import_id)
            if stored is None:
                return []
            email_id = stored.record.email_id

        out = []
        for offer in self.offers.values():
            if offer.tenant_id != tenant_id or not offer.needs_review:
                continue
            if email_id is not None and offer.source_email_id != email_id:
                continue
            counterparty = self.counterparties.get(offer.counterparty_id)
            out.append(
                {
                    "id": offer.id,
                    "side": offer.side,
                    "description": offer.description,
                    "quantity": offer.quantity,
                    "unit_price": offer.unit_price,
                    "currency": offer.currency,
                    "confidence": offer.confidence,
                    "review_reason": offer.review_reason,
                    "source_ref": offer.source_ref,
                    "source_email_id": offer.source_email_id,
                    "counterparties": {"name": counterparty.name} if counterparty else None,
                }
            )
        return out

    def resolve_row(self, tenant_id: str, offer_id: str, changes: dict) -> bool:
        offer = self.offers.get(offer_id)
        if offer is None or offer.tenant_id != tenant_id:
            return False

        for name in ("description", "quantity", "unit_price", "currency"):
            if changes.get(name) is not None:
                setattr(offer, name, changes[name])

        offer.needs_review = False
        offer.review_reason = None
        offer.confidence = 1.0
        return True

    def list_suppliers(self, tenant_id: str) -> list[SupplierSummary]:
        out = []
        for cp in self.counterparties.values():
            live = [
                o for o in self.offers.values()
                if o.counterparty_id == cp.id and o.tenant_id == tenant_id and o.status == "live"
            ]
            out.append(
                SupplierSummary(
                    id=cp.id,
                    primary_email=cp.primary_email,
                    name=cp.name,
                    default_currency=cp.default_currency,
                    decimal_separator=cp.decimal_separator,
                    staleness_hours=cp.staleness_hours,
                    typical_row_count=cp.typical_row_count,
                    live_offers=len(live),
                    last_seen_at=max((o.last_confirmed_at for o in live), default=None),
                    sample_prices=[
                        f"{o.unit_price} {o.currency or ''}".strip()
                        for o in live[:5]
                        if o.unit_price is not None
                    ],
                )
            )
        return out

    def update_supplier(self, tenant_id: str, supplier_id: str, changes: dict) -> bool:
        cp = self.counterparties.get(supplier_id)
        allowed = {k: v for k, v in changes.items() if k in self._SUPPLIER_SETTABLE}
        if cp is None or not allowed:
            return False
        self.counterparties[supplier_id] = replace(cp, **allowed)
        return True

    def count_emails(self, tenant_id: str) -> int:
        return sum(1 for r in self.emails.values() if r.tenant_id == tenant_id)

    def get_stored_email(self, tenant_id: str, email_id: str) -> StoredEmail | None:
        record = self.emails.get(email_id)
        if record is None or record.tenant_id != tenant_id:
            return None

        return StoredEmail(
            id=email_id,
            tenant_id=record.tenant_id,
            message_id=record.message_id,
            from_email=record.from_email,
            subject=record.subject,
            received_at=record.received_at,
            body_text=record.body_text,
            body_raw=record.body_raw,
            attachments=list(record.attachments),
            counterparty_id=record.counterparty_id,
            classification=record.classification,
        )

    def attribute_email(
        self,
        tenant_id: str,
        email_id: str,
        counterparty_id: str | None = None,
        classification: str | None = None,
    ) -> bool:
        record = self.emails.get(email_id)
        if record is None or record.tenant_id != tenant_id:
            return False

        changes: dict = {}
        if counterparty_id is not None:
            changes["counterparty_id"] = counterparty_id
            changes["counterparty_method"] = "manual"
            changes["counterparty_confidence"] = 1.0
            changes["needs_sender_review"] = False
        if classification is not None:
            changes["classification"] = classification

        self.emails[email_id] = replace(record, **changes)
        return True

    # ---------------------------------------------------------------- helpers

    def live_offers(self) -> list[StoredOffer]:
        return [o for o in self.offers.values() if o.status == "live"]

    def age_all_offers(self, hours: int) -> None:
        """Push every offer's confirmation time back, to rehearse a later morning."""
        for offer in self.offers.values():
            offer.last_confirmed_at -= timedelta(hours=hours)
