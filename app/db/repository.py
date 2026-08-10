"""What the pipeline needs from storage, expressed as a protocol.

The pipeline depends on this interface rather than on Supabase. Two reasons, and the
second is the important one.

It makes the whole pipeline testable end to end with no database, no network and no
credentials — which means the reconciliation logic, the part where a mistake silently
destroys data, is exercised on every commit rather than only against a live project.

And it keeps the trading logic free of storage concerns. If the decision to close an
offer lived inside a database call, the only way to test it would be to close a real
offer.
"""

from __future__ import annotations

from typing import Protocol

from app.brain.reconcile import Action, ExistingOffer, IncomingOffer
from app.db.models import CounterpartyConfig, EmailRecord, ImportRecord, TenantConfig


class Repository(Protocol):
    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        ...

    def find_counterparty(self, tenant_id: str, email: str) -> CounterpartyConfig | None:
        ...

    def create_counterparty(
        self, tenant_id: str, email: str, name: str | None
    ) -> CounterpartyConfig:
        ...

    def email_already_seen(self, tenant_id: str, message_id: str) -> bool:
        """Retries and mailbox re-syncs redeliver the same message.

        Without this, a redelivery is reconciled a second time. That is not merely
        wasteful: the second pass sees the offers it just created as already live, and
        any row missing from the redelivered copy gets closed.
        """
        ...

    def save_email(self, record: EmailRecord) -> str:
        ...

    def load_live_offers(
        self, tenant_id: str, counterparty_id: str, side: str
    ) -> list[ExistingOffer]:
        ...

    def previous_row_count(self, tenant_id: str, counterparty_id: str) -> int | None:
        """Rows in this sender's last successful import — the baseline for the guard
        that stops a broken parse from closing their entire stock."""
        ...

    def apply_actions(
        self,
        tenant_id: str,
        counterparty_id: str,
        email_id: str | None,
        side: str,
        actions: list[Action],
        incoming_by_key: dict[str, IncomingOffer],
    ) -> None:
        ...

    def record_import(self, record: ImportRecord) -> str:
        ...
