"""One email, end to end.

    dedupe -> resolve sender -> classify -> extract -> normalise -> reconcile -> persist

Every stage can decline. An email whose sender cannot be established, or whose side
cannot be decided, is stored and flagged rather than guessed at and filed. That
asymmetry runs through the whole module: an email sitting in a review queue costs
somebody a click, while a supplier's catalogue filed against the wrong counterparty
corrupts two boards at once — one gaining rows it never sent, the other having live
stock closed as sold.

Nothing here talks to Supabase directly; it works through the Repository protocol, so
the entire pipeline is exercised in tests against an in-memory store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.brain.classify import classify_side
from app.brain.completeness import detect_list_completeness
from app.brain.normalise import expand_variants, parse_product
from app.brain.reconcile import IncomingOffer, ReconcileResult, reconcile
from app.brain.sender import resolve_sender
from app.brain.tables import parse_grid, read_html_tables
from app.db.models import EmailRecord, ImportRecord
from app.db.repository import Repository
from app.schemas.email import InboundEmail

log = logging.getLogger(__name__)

Outcome = str  # 'processed' | 'duplicate' | 'needs_sender_review' | 'unclassified' | 'no_rows'


@dataclass
class PipelineResult:
    outcome: Outcome
    message_id: str
    email_id: str | None = None
    counterparty_id: str | None = None
    side: str | None = None
    row_count: int = 0
    expanded_rows: int = 0
    tables_needing_mapping: int = 0
    reconcile: ReconcileResult | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def processed(self) -> bool:
        return self.outcome == "processed"


def process_email(payload: InboundEmail, repo: Repository) -> PipelineResult:
    """Run one inbound email through the whole brain and persist the result."""
    tenant = repo.get_tenant(payload.tenant_id)
    if tenant is None:
        return PipelineResult("unknown_tenant", payload.message_id, notes=["no such tenant"])

    # Retries and mailbox re-syncs redeliver the same message. Reconciling twice is not
    # merely wasteful — the second pass sees the offers it just created as already live,
    # so anything missing from the redelivered copy gets closed.
    if repo.email_already_seen(tenant.id, payload.message_id):
        return PipelineResult("duplicate", payload.message_id, notes=["already processed"])

    sender = resolve_sender(
        payload.from_email,
        payload.subject,
        payload.body_text or payload.body_raw,
        internal_domains=tenant.internal_domains,
        internal_addresses=tenant.internal_addresses,
    )

    classification = classify_side(payload.subject, payload.body_text)

    counterparty = None
    if sender.email and not sender.needs_review:
        counterparty = repo.find_counterparty(tenant.id, sender.email)
        if counterparty is None:
            counterparty = repo.create_counterparty(tenant.id, sender.email, sender.name)

    email_id = repo.save_email(
        EmailRecord(
            tenant_id=tenant.id,
            message_id=payload.message_id,
            from_email=payload.from_email,
            received_at=payload.received_at,
            subject=payload.subject,
            body_text=payload.body_text,
            body_raw=payload.body_raw,
            counterparty_id=counterparty.id if counterparty else None,
            counterparty_method=sender.method,
            counterparty_confidence=sender.confidence,
            needs_sender_review=sender.needs_review or counterparty is None,
            suggested_sender_name=sender.name,
            classification=classification.side or "unclassified",
        )
    )

    if counterparty is None:
        # Common for WhatsApp offers a staff member retyped: no supplier address exists
        # anywhere in the message. The email is kept; a human attributes it in a click.
        return PipelineResult(
            "needs_sender_review",
            payload.message_id,
            email_id=email_id,
            notes=[f"sender unresolved ({sender.method}); nothing reconciled"],
        )

    if classification.side is None:
        # Filing a supplier's price list as buyer demand would have the client chasing
        # people to sell them stock those people are themselves selling.
        return PipelineResult(
            "unclassified",
            payload.message_id,
            email_id=email_id,
            counterparty_id=counterparty.id,
            notes=[classification.reason],
        )

    rows, expanded, unmapped = extract_rows(payload, counterparty.decimal_separator)

    incoming = [
        IncomingOffer(
            identity_key=spec.identity_key(),
            quantity=row.quantity,
            unit_price=row.price,
            currency=row.currency or counterparty.default_currency,
            description=row.description,
            source_ref=row.source_ref,
        )
        for row, spec in (
            (r, parse_product(r.description, ean=r.ean, colour=r.colour)) for r in rows
        )
    ]

    is_complete, why = detect_list_completeness(
        payload.subject, payload.body_text, len(incoming), counterparty.typical_row_count
    )

    previous = repo.previous_row_count(tenant.id, counterparty.id)
    existing = repo.load_live_offers(tenant.id, counterparty.id, classification.side)

    result = reconcile(
        existing,
        incoming,
        previous_row_count=previous,
        is_complete_list=is_complete,
    )
    result.notes.append(f"completeness: {why}")

    incoming_by_key = {item.identity_key: item for item in incoming}
    repo.apply_actions(
        tenant.id, counterparty.id, email_id, classification.side, result.actions,
        incoming_by_key,
    )

    repo.record_import(
        ImportRecord(
            tenant_id=tenant.id,
            counterparty_id=counterparty.id,
            email_id=email_id,
            row_count=result.row_count,
            previous_row_count=previous,
            is_complete_list=is_complete,
            status=result.status,
            offers_inserted=result.inserted,
            offers_updated=result.updated,
            offers_refreshed=result.refreshed,
            offers_closed=result.closed,
            rows_expanded_from_variants=expanded,
            duplicate_rows=result.duplicate_rows,
            notes="; ".join(result.notes),
        )
    )

    log.info(
        "processed tenant=%s message_id=%s side=%s rows=%d "
        "inserted=%d updated=%d refreshed=%d closed=%d status=%s",
        tenant.id, payload.message_id, classification.side, result.row_count,
        result.inserted, result.updated, result.refreshed, result.closed, result.status,
    )

    return PipelineResult(
        "processed" if incoming else "no_rows",
        payload.message_id,
        email_id=email_id,
        counterparty_id=counterparty.id,
        side=classification.side,
        row_count=len(incoming),
        expanded_rows=expanded,
        tables_needing_mapping=unmapped,
        reconcile=result,
        notes=result.notes,
    )


def extract_rows(payload: InboundEmail, decimal_hint: str | None):
    """Pull line items from the body's tables, its attachments, or failing both, prose.

    Tables are tried first and prose only when they yield nothing. Most emails carry
    both — a price list plus a covering note — and parsing the note as well would
    duplicate rows that the table already captured.
    """
    grids = list(read_html_tables(_html_of(payload)))

    for attachment in payload.attachments:
        if attachment.rows:
            from app.brain.tables.reader import Grid

            grids.append(Grid(rows=attachment.rows, source=attachment.filename))

    rows = []
    unmapped = 0
    for grid in grids:
        table = parse_grid(grid, decimal_hint=decimal_hint)
        if table.needs_column_mapping:
            unmapped += 1
            continue
        rows.extend(table.rows)

    if rows:
        return rows, 0, unmapped

    rows, expanded = _rows_from_prose(payload.body_text, decimal_hint)
    return rows, expanded, unmapped


def _html_of(payload: InboundEmail) -> str:
    """The body when it is HTML. Table extraction needs markup, not flattened text."""
    text = payload.body_raw or payload.body_text or ""
    return text if "<table" in text.lower() else ""


def _rows_from_prose(body: str | None, decimal_hint: str | None):
    """Bullet-list offers: 'A17 LTE DS SM-A175 4+128 — Black / Blue — €125'."""
    from app.brain.normalise import parse_price, parse_quantity
    from app.brain.tables.parser import TableRow

    rows: list[TableRow] = []
    expanded = 0

    for number, line in enumerate(( body or "").splitlines(), start=1):
        line = line.strip(" •\t-")
        if len(line) < 8:
            continue

        variants = expand_variants(line)
        if len(variants) > 1:
            expanded += len(variants) - 1

        for variant in variants:
            price = parse_price(variant.text, decimal_hint=decimal_hint)
            if price is None:
                continue
            rows.append(
                TableRow(
                    description=variant.text,
                    colour=variant.colour,
                    quantity=parse_quantity(variant.text),
                    price=price,
                    source_ref=f"body line {number}",
                )
            )

    return rows, expanded
