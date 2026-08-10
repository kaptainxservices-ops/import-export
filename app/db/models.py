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
    notes: str = ""
