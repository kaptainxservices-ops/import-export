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


class Repository(Protocol):
    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        ...

    def find_counterparty(self, tenant_id: str, email: str) -> CounterpartyConfig | None:
        ...

    def get_counterparty(self, tenant_id: str, counterparty_id: str) -> CounterpartyConfig | None:
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

    def record_usage(self, events: list[UsageEvent]) -> None:
        """Token spend, per tenant. Accounting only — nothing reads it to make a
        decision, so a failure to write it must never fail the email that earned it."""
        ...

    # ---------------------------------------------------------------- matching

    def tenant_for_user(self, user_id: str) -> str | None:
        """Which tenant a signed-in user belongs to.

        Resolved server-side from `profiles`, never taken from the request. A tenant id
        sent by the browser is a request to read someone else's board.
        """
        ...

    def get_board_offer(self, tenant_id: str, offer_id: str) -> BoardOffer | None:
        ...

    def load_board(self, tenant_id: str, side: str, limit: int = 5000) -> list[BoardOffer]:
        """Every live offer on one side, for matching against."""
        ...

    # ---------------------------------------------------------------- review

    def list_review_queue(self, tenant_id: str, limit: int = 200) -> list[ReviewItem]:
        """Emails the pipeline declined to file, newest first."""
        ...

    # ---------------------------------------------------------------- deals

    def list_deals(self, tenant_id: str, include_closed: bool = False) -> list[Deal]:
        """Deals with their allocations, newest first."""
        ...

    def get_deal(self, tenant_id: str, deal_id: str) -> Deal | None:
        ...

    def create_deal(self, tenant_id: str, title: str | None, status: str) -> Deal:
        ...

    def update_deal(self, tenant_id: str, deal_id: str, changes: dict) -> bool:
        ...

    def commitments_on_offer(self, tenant_id: str, offer_id: str) -> list[tuple]:
        """Every existing claim on one lot, as (deal_id, reference, quantity, is_open).

        Read before an allocation is written. Over-allocating a lot is refused and
        double-committing one is warned about, and neither is answerable without knowing
        what the other deals already hold.
        """
        ...

    def add_allocation(self, tenant_id: str, deal_id: str, leg: dict) -> Allocation:
        ...

    def remove_allocation(self, tenant_id: str, allocation_id: str) -> bool:
        ...

    # ---------------------------------------------------------------- suppliers

    def list_suppliers(self, tenant_id: str) -> list[SupplierSummary]:
        """Every counterparty, with the settings that govern how their lists are read."""
        ...

    def update_supplier(self, tenant_id: str, supplier_id: str, changes: dict) -> bool:
        """Change a supplier's parsing settings. False if they are not on this board."""
        ...

    def count_emails(self, tenant_id: str) -> int:
        """How many emails have been seen at all.

        Only used to tell the two empty queues apart. "Nothing needs review" and
        "nothing has been loaded" look identical on screen and mean opposite things —
        one is the system working, the other is the system not running.
        """
        ...

    def get_stored_email(self, tenant_id: str, email_id: str) -> StoredEmail | None:
        """An email read back out, complete enough to run extraction over again."""
        ...

    def attribute_email(
        self,
        tenant_id: str,
        email_id: str,
        counterparty_id: str | None = None,
        classification: str | None = None,
    ) -> bool:
        """Record a human's answer to what the pipeline would not guess.

        Returns False when the email does not belong to this tenant, which is the check
        that stops an id from one client's board resolving an email on another's.
        """
        ...
