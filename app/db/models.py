"""Configuration and record shapes the pipeline reads and writes.

These mirror the database tables but are plain dataclasses, so the brain never imports
a database client. That is what lets the whole pipeline be tested end to end against an
in-memory store, with no Supabase project and no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TenantConfig:
    id: str
    name: str = ""
    # Which addresses are the client's own. Cannot be detected — a supplier's domain
    # and a colleague's domain look identical — so it is configuration.
    internal_domains: set[str] = field(default_factory=set)
    internal_addresses: set[str] = field(default_factory=set)
    staleness_hours: int = 24


@dataclass(frozen=True)
class CounterpartyConfig:
    id: str
    primary_email: str
    name: str | None = None
    # Resolves a bare '$' and an ambiguous '1,079'. Set per supplier because neither
    # can be settled from the text alone.
    default_currency: str | None = None
    decimal_separator: str | None = None
    typical_row_count: int | None = None
    staleness_hours: int | None = None


@dataclass(frozen=True)
class EmailRecord:
    tenant_id: str
    message_id: str
    from_email: str
    received_at: datetime
    subject: str = ""
    body_text: str = ""
    body_raw: str = ""
    counterparty_id: str | None = None
    counterparty_method: str = "envelope"
    counterparty_confidence: float = 1.0
    needs_sender_review: bool = False
    suggested_sender_name: str | None = None
    classification: str = "unclassified"
    # Stored so an email can be reprocessed after a human attributes it. Without them a
    # spreadsheet's rows exist only in the request that delivered it, and resolving the
    # sender a minute later would file the email against the right supplier with none of
    # their stock attached to it.
    attachments: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class StoredEmail:
    """An email read back out, in the shape reprocessing needs."""

    id: str
    tenant_id: str
    message_id: str
    from_email: str
    subject: str
    received_at: datetime
    body_text: str
    body_raw: str
    attachments: list[dict]
    counterparty_id: str | None
    classification: str


@dataclass(frozen=True)
class SupplierSummary:
    """A counterparty with its settings and enough history to set them by.

    The settings on their own are unanswerable — nobody knows offhand whether Masterfone
    writes '1,079' meaning a thousand or meaning one. What makes them answerable is
    seeing a few of that supplier's actual prices next to the switch.
    """

    id: str
    primary_email: str
    name: str | None = None
    country: str | None = None
    default_currency: str | None = None
    decimal_separator: str | None = None
    staleness_hours: int | None = None
    typical_row_count: int | None = None
    live_offers: int = 0
    last_seen_at: datetime | None = None
    sample_prices: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewItem:
    """An email the system declined to file, and why.

    The asymmetry this exists to serve: an email in a queue costs somebody a click, and
    a supplier's catalogue filed against the wrong counterparty corrupts two boards at
    once. Every stage of the pipeline is allowed to decline, and this is where the
    declining shows up.
    """

    id: str
    received_at: datetime
    from_email: str
    subject: str
    classification: str
    needs_sender_review: bool
    counterparty_id: str | None = None
    counterparty_name: str | None = None
    counterparty_method: str = "envelope"
    counterparty_confidence: float = 1.0
    suggested_sender_name: str | None = None
    body_preview: str = ""
    attachment_count: int = 0

    @property
    def needs_classification(self) -> bool:
        return self.classification == "unclassified"

    @property
    def reason(self) -> str:
        if self.needs_sender_review and self.needs_classification:
            return "both"
        return "sender" if self.needs_sender_review else "classification"


@dataclass(frozen=True)
class BoardOffer:
    """A live row, in the shape matching needs.

    Separate from the reconciliation types on purpose: reconciliation cares about
    identity and change, matching cares about specification and price, and conflating
    them means every change to one drags the other along.
    """

    id: str
    counterparty_id: str
    counterparty_name: str | None
    side: str
    description: str
    quantity: int | None
    unit_price: float | None
    currency: str | None
    identity_key: str
    match_key: str | None
    description_key: str | None
    ean: str | None
    brand: str | None
    capacity_gb: int | None
    colour: str | None
    country: str | None = None
    # Carried because matching uses it as a hard gate: a case is not a phone.
    category: str | None = None


@dataclass(frozen=True)
class Allocation:
    """One leg of a deal — a quantity of one line item, at a price, on one side.

    The numbers are frozen here rather than read through to the offer. A supplier's list
    expires every morning; a deal struck yesterday at 905 did not, and a deal whose
    figures move under it is a deal nobody can invoice against.
    """

    id: str
    deal_id: str
    side: str                     # 'buy' from a seller, 'sell' to a buyer
    counterparty_id: str
    counterparty_name: str | None = None
    offer_id: str | None = None
    description: str = ""
    quantity: int = 0
    unit_price: float | None = None
    currency: str | None = None
    note: str | None = None

    @property
    def value(self) -> float | None:
        return None if self.unit_price is None else round(self.unit_price * self.quantity, 2)


@dataclass(frozen=True)
class Deal:
    """A wrapper referencing line items. It never relocates them.

    The same lot may belong to two deals and remains a price-history point either way,
    so nothing here moves a row out of the Sellers or Buyers list.
    """

    id: str
    tenant_id: str
    reference: int
    title: str | None = None
    status: str = ""
    status_updated_at: datetime | None = None
    owner_id: str | None = None
    keep_offers_visible: bool = True
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    outcome: str | None = None
    quoted_unit_price: float | None = None
    loss_reason: str | None = None
    allocations: list[Allocation] = field(default_factory=list)

    @property
    def buying(self) -> list[Allocation]:
        return [a for a in self.allocations if a.side == "buy"]

    @property
    def selling(self) -> list[Allocation]:
        return [a for a in self.allocations if a.side == "sell"]

    @property
    def margin(self) -> float | None:
        """Sell value less buy value, or None if either side is incomplete.

        None rather than a partial figure: a deal with a buyer agreed and no supplier yet
        has a revenue, not a margin, and showing one would be the same error as inventing
        an exchange rate.
        """
        if not self.buying or not self.selling:
            return None
        if any(a.unit_price is None for a in self.allocations):
            return None

        bought = sum(a.value or 0 for a in self.buying)
        sold = sum(a.value or 0 for a in self.selling)
        return round(sold - bought, 2)

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


@dataclass(frozen=True)
class UsageEvent:
    """One model call, priced.

    Anthropic is the only cost that scales linearly with clients, so it is the only one
    recorded per tenant. Written from the first call rather than added later, because
    "is this client profitable?" should be a query, and a retry loop should show up as a
    spike rather than as a surprise at the end of the month.
    """

    tenant_id: str
    operation: str  # 'classify' | 'extract' | 'columns' | 'personalise'
    model: str
    email_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int | None = None
    succeeded: bool = True
    error: str | None = None


@dataclass(frozen=True)
class ImportRecord:
    tenant_id: str
    counterparty_id: str | None
    email_id: str | None
    row_count: int
    previous_row_count: int | None
    is_complete_list: bool | None
    status: str
    offers_inserted: int = 0
    offers_updated: int = 0
    offers_refreshed: int = 0
    offers_closed: int = 0
    rows_expanded_from_variants: int = 0
    duplicate_rows: int = 0
    # How many rows a person is being asked to look at, and what is wrong with the list
    # as a whole. The summary is what makes a 500-row import reviewable at all.
    flagged_rows: int = 0
    parse_warnings: list[str] = field(default_factory=list)
    notes: str = ""
