"""An in-memory Repository, used by the tests.

This is not a mock that records calls and asserts on them. It is a working store that
holds offers, applies reconciliation actions, keeps a change log and enforces the same
identity rule as the database. That means a pipeline test proves the offers really do
end up in the right state — which is the only claim worth making about reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import count

from app.brain.reconcile import Action, ExistingOffer, IncomingOffer
from app.db.models import CounterpartyConfig, EmailRecord, ImportRecord, TenantConfig


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
    first_seen_at: datetime = field(default_factory=datetime.utcnow)
    last_confirmed_at: datetime = field(default_factory=datetime.utcnow)


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
        self.imports: list[ImportRecord] = []
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
        for record in reversed(self.imports):
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
        self.imports.append(record)
        return self._next("import")

    # ---------------------------------------------------------------- helpers

    def live_offers(self) -> list[StoredOffer]:
        return [o for o in self.offers.values() if o.status == "live"]

    def age_all_offers(self, hours: int) -> None:
        """Push every offer's confirmation time back, to rehearse a later morning."""
        for offer in self.offers.values():
            offer.last_confirmed_at -= timedelta(hours=hours)
