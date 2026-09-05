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
import re
from dataclasses import dataclass, field

from app.brain.classify import Classification, classify_side
from app.brain.completeness import detect_list_completeness
from app.brain.confidence import score_row
from app.brain.llm import UsageLog, classify_side_llm, mapper_for
from app.brain.normalise import detect_declared_currency, expand_variants, parse_product
from app.brain.quoting import strip_quoted_history
from app.brain.reconcile import IncomingOffer, ReconcileResult, reconcile
from app.brain.sender import resolve_sender
from app.brain.tables import ColumnMapper, parse_grid, read_html_tables
from app.db.models import EmailRecord, ImportRecord, UsageEvent
from app.db.repository import Repository
from app.schemas.email import Attachment, InboundEmail

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
    model_calls: int = 0
    model_cost_usd: float = 0.0

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

    usage = UsageLog()
    classification = _decide_side(payload, usage)

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
            attachments=[a.model_dump() for a in payload.attachments],
        )
    )

    # Written as soon as there is an email to attach it to, and before any of the ways
    # out below. A call that has been paid for is a call that gets recorded, whatever
    # the pipeline decides afterwards.
    written = _record_usage(repo, tenant.id, email_id, usage)

    if counterparty is None:
        # Common for WhatsApp offers a staff member retyped: no supplier address exists
        # anywhere in the message. The email is kept; a human attributes it in a click.
        return PipelineResult(
            "needs_sender_review",
            payload.message_id,
            email_id=email_id,
            notes=[f"sender unresolved ({sender.method}); nothing reconciled"],
            model_calls=len(usage),
            model_cost_usd=usage.cost_usd,
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
            model_calls=len(usage),
            model_cost_usd=usage.cost_usd,
        )

    return _extract_and_reconcile(
        payload, tenant.id, counterparty, classification.side, email_id, repo, usage, written
    )


def reprocess_email(tenant_id: str, email_id: str, repo: Repository) -> PipelineResult:
    """Run an already-stored email through extraction again.

    Called after a human resolves what the pipeline declined to guess: which supplier
    sent it, or which side of the trade it is. It deliberately does *not* re-run
    classification or sender resolution — a person has just overruled both, and asking
    the rules again would overwrite the answer with the one that was already wrong.

    The email row is not written again, so the deduplication guard stays honest. Nothing
    here can run for an email that is still missing a counterparty or a side, because
    there would be nothing to reconcile against.
    """
    stored = repo.get_stored_email(tenant_id, email_id)
    if stored is None:
        return PipelineResult("not_found", "", notes=["no such email on this tenant"])

    if stored.counterparty_id is None:
        return PipelineResult(
            "needs_sender_review", stored.message_id, email_id=email_id,
            notes=["still no counterparty"],
        )

    if stored.classification not in ("sell", "buy"):
        return PipelineResult(
            "unclassified", stored.message_id, email_id=email_id,
            counterparty_id=stored.counterparty_id,
            notes=["still unclassified"],
        )

    counterparty = repo.get_counterparty(tenant_id, stored.counterparty_id)
    if counterparty is None:
        return PipelineResult(
            "needs_sender_review", stored.message_id, email_id=email_id,
            notes=["counterparty no longer exists"],
        )

    payload = InboundEmail(
        tenant_id=tenant_id,
        message_id=stored.message_id,
        from_email=stored.from_email,
        subject=stored.subject,
        received_at=stored.received_at,
        body_text=stored.body_text,
        body_raw=stored.body_raw,
        attachments=[Attachment(**a) for a in stored.attachments],
    )

    usage = UsageLog()
    return _extract_and_reconcile(
        payload, tenant_id, counterparty, stored.classification, email_id, repo, usage, 0
    )


def _extract_and_reconcile(
    payload: InboundEmail,
    tenant_id: str,
    counterparty,
    side: str,
    email_id: str | None,
    repo: Repository,
    usage: UsageLog,
    written: int,
) -> PipelineResult:
    """The half of the pipeline that turns a known sender and side into board rows.

    Shared with `reprocess_email` on purpose. A second copy of extraction and
    reconciliation, reached only when a human has intervened, would be the copy nobody
    tests and the one that quietly closes a supplier's stock.
    """
    # n8n is supposed to have done this already. It is done again because n8n is a GUI
    # the client can edit, and a step that silently stops happening there would show up
    # as last week's prices quietly re-entering the board as this week's. Idempotent: an
    # already-clean body has no markers to find.
    body, why_body = strip_quoted_history(payload.body_text)
    if body != payload.body_text:
        payload = payload.model_copy(update={"body_text": body})

    # 'Prices in EUR' stated once at the top governs every bare number beneath it.
    # Read per row, a list like that reaches the board with no currency anywhere, and an
    # offer with no currency cannot be compared against anything.
    declared = detect_declared_currency(payload.subject, body)

    rows, expanded, unmapped = extract_rows(
        payload, counterparty.decimal_separator, mapper_for(usage), declared
    )
    _record_usage(repo, tenant_id, email_id, usage, since=written)

    # Scored per row before anything is written. Two separate answers come back: how much
    # we inferred, which is stored on the row, and whether a person could usefully fix it,
    # which decides whether it reaches the live board at all.
    scores = [
        score_row(
            description=row.description,
            price=row.price,
            raw_price=str(row.price) if row.price is not None else None,
            currency=row.currency or declared or counterparty.default_currency,
            currency_source=(
                "row" if row.currency
                else "email" if declared
                else "supplier" if counterparty.default_currency
                else "none"
            ),
            quantity=row.quantity,
            raw_quantity=str(row.quantity) if row.quantity is not None else None,
            brand=spec.brand or row.brand,
            has_ean=bool(spec.ean),
            has_decimal_hint=bool(counterparty.decimal_separator),
            side=side,
        )
        for row, spec in (
            (r, parse_product(r.description, ean=r.ean, colour=r.colour)) for r in rows
        )
    ]

    flagged = sum(1 for c in scores if c.needs_review)
    # Deduplicated: 'no currency stated anywhere in this list' is one fact about the
    # import, however many rows it is true of.
    parse_warnings = sorted({w for c in scores for w in c.list_reasons})

    incoming = [
        IncomingOffer(
            identity_key=spec.identity_key(),
            quantity=row.quantity,
            unit_price=row.price,
            # Order of authority: what this row said, then what the email declared for
            # all its rows, then what this supplier normally quotes. A supplier who
            # usually bills in USD but headed today's list 'PRICE IN EUR' meant EUR.
            currency=row.currency or declared or counterparty.default_currency,
            description=row.description,
            source_ref=row.source_ref,
            # These become real columns. The database rebuilds identity_key from them
            # as a generated column, so omitting them would key every row identically.
            fields={
                "brand": spec.brand or row.brand,
                "category": spec.category or row.category,
                "ean": spec.ean,
                "description_key": spec.description_key,
                "capacity_gb": spec.capacity_gb,
                "ram_gb": spec.ram_gb,
                "colour": spec.colour,
                "network": spec.network,
                "dual_sim": spec.dual_sim,
                "edition": spec.edition,
                # Grade and region were parsed and then dropped on the floor: both have
                # had columns since the first migration and nothing ever wrote to them.
                # 'A/A+' and 'ZP/A' are half of what a trader reads before quoting.
                "grade": spec.grade,
                "region_code": spec.region_code,
                # EXW and FOB are not the same number. A board that prints one price for
                # both is comparing a factory-gate cost against a delivered one.
                "incoterm": row.incoterm,
                "price_basis": row.price_basis or "unknown",
                "confidence": round(scored.overall, 2),
                "confidence_json": scored.fields,
                "needs_review": scored.needs_review,
                "review_reason": scored.reason or None,
            },
        )
        for row, spec, scored in (
            (r, parse_product(r.description, ean=r.ean, colour=r.colour), c)
            # strict: rows and scores are built from the same list one line above, so a
            # length mismatch is a bug rather than a case to tolerate quietly.
            for r, c in zip(rows, scores, strict=True)
        )
    ]

    is_complete, why = detect_list_completeness(
        payload.subject, payload.body_text, len(incoming), counterparty.typical_row_count
    )

    previous = repo.previous_row_count(tenant_id, counterparty.id)
    existing = repo.load_live_offers(tenant_id, counterparty.id, side)

    result = reconcile(
        existing,
        incoming,
        previous_row_count=previous,
        is_complete_list=is_complete,
    )
    result.notes.append(f"completeness: {why}")
    if "no quoted reply" not in why_body:
        result.notes.append(f"body: {why_body}")

    incoming_by_key = {item.identity_key: item for item in incoming}
    repo.apply_actions(
        tenant_id, counterparty.id, email_id, side, result.actions,
        incoming_by_key,
    )

    repo.record_import(
        ImportRecord(
            tenant_id=tenant_id,
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
            flagged_rows=flagged,
            parse_warnings=parse_warnings,
            notes="; ".join(result.notes),
        )
    )

    log.info(
        "processed tenant=%s message_id=%s side=%s rows=%d "
        "inserted=%d updated=%d refreshed=%d closed=%d status=%s",
        tenant_id, payload.message_id, side, result.row_count,
        result.inserted, result.updated, result.refreshed, result.closed, result.status,
    )

    return PipelineResult(
        "processed" if incoming else "no_rows",
        payload.message_id,
        email_id=email_id,
        counterparty_id=counterparty.id,
        side=side,
        row_count=len(incoming),
        expanded_rows=expanded,
        tables_needing_mapping=unmapped,
        reconcile=result,
        notes=result.notes,
        model_calls=len(usage),
        model_cost_usd=usage.cost_usd,
    )


# ---------------------------------------------------------------- the model, if any


def _decide_side(payload: InboundEmail, usage: UsageLog) -> Classification:
    """The rules first, and the model only where they came up short.

    Two situations reach a model, and they are treated differently.

    When the rules found nothing at all — no WTS, no WTB, no price list, nothing — there
    is no answer to overturn, so the model's stands. That is the forwarded WhatsApp
    message and the bare table with a two-word covering note.

    When the rules leant one way but weakly, the model is a second opinion, and a second
    opinion that *disagrees* leaves us with two guesses and no reason to prefer either.
    Filing on a coin toss is exactly how a supplier's catalogue ends up on the board as
    customer demand, so a disagreement refuses instead: side None, review queue, one
    click. The rules keep their answer only when the model agrees with it.

    The line is drawn at the subject. A subject saying 'WTS Pricelist 24.07' scores 0.95
    and is never questioned — the trade is consistent about this, and asking anyway would
    be a bill that scales with the inbox and buys nothing. Anything decided from the body
    alone is questioned, because a buyer's signature reading 'price list available on
    request' matches a sell rule perfectly, and that email is not an offer.
    """
    classification = classify_side(payload.subject, payload.body_text)

    settled = (
        classification.side is not None
        and classification.confidence >= 0.9
        and not classification.needs_llm
    )
    if settled:
        return classification

    verdict = classify_side_llm(payload.subject, payload.body_text)
    usage.add("classify", verdict.answer if verdict else None)

    if verdict is None or verdict.side is None:
        if classification.side is None:
            return classification
        return Classification(
            classification.side,
            classification.confidence,
            f"{classification.reason}; model had no opinion",
            True,
        )

    if classification.side is None:
        return Classification(verdict.side, 0.8, "model read the message as " + verdict.side)

    if verdict.side == classification.side:
        return Classification(
            classification.side, 0.9, f"{classification.reason}; model agrees"
        )

    return Classification(
        None,
        0.0,
        f"rules read this as {classification.side}, the model as {verdict.side}; "
        "neither is confident enough to file it",
        True,
    )


def _record_usage(
    repo: Repository, tenant_id: str, email_id: str | None, usage: UsageLog, since: int = 0
) -> int:
    """Persist the model calls made so far, and report how many are now written."""
    pending = usage.calls[since:]
    if pending:
        repo.record_usage(
            [
                UsageEvent(
                    tenant_id=tenant_id,
                    email_id=email_id,
                    operation=operation,
                    model=answer.model,
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                    cost_usd=answer.cost_usd,
                    duration_ms=answer.duration_ms,
                )
                for operation, answer in pending
            ]
        )
    return len(usage.calls)


def extract_rows(
    payload: InboundEmail,
    decimal_hint: str | None,
    column_mapper: ColumnMapper | None = None,
    default_currency: str | None = None,
):
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
        table = parse_grid(
            grid,
            decimal_hint=decimal_hint,
            default_currency=default_currency,
            column_mapper=column_mapper,
        )
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


# Vadimpex and others mark quantity and price with asterisks:
#     Samsung A57 5G DS 8/128GB A576 *288* Navy *259€*
# The first group is a count, the second is money. Without reading the convention, the
# whole line is description and every number in it competes to be the price.
_MARKER = re.compile(r"\*\s*([^*]{1,24}?)\s*\*")
_MARKER_IS_MONEY = re.compile(r"[€$£]|\b(?:eur|usd|gbp)\b", re.IGNORECASE)


def _rows_from_prose(body: str | None, decimal_hint: str | None):
    """Bullet-list offers: 'A17 LTE DS SM-A175 4+128 — Black / Blue — €125'.

    A line only becomes an offer if something in it is marked as money. That is a
    deliberate floor: a sentence full of numbers is not a price list, and guessing
    which number is the price is how 'A57 5G' became a €575 handset.
    """
    from app.brain.normalise import (
        detect_incoterm,
        detect_price_basis,
        find_price,
        normalise_currency,
        parse_quantity,
        strip_marked_prices,
    )
    from app.brain.tables.parser import TableRow

    rows: list[TableRow] = []
    expanded = 0

    for number, line in enumerate((body or "").splitlines(), start=1):
        line = line.strip(" •\t-")
        if len(line) < 8:
            continue

        variants = expand_variants(line)
        if len(variants) > 1:
            expanded += len(variants) - 1

        for variant in variants:
            price = find_price(variant.text, decimal_hint=decimal_hint)
            if price is None:
                continue

            quantity, description = _quantity_from_markers(variant.text)
            description = strip_marked_prices(description)
            rows.append(
                TableRow(
                    description=description,
                    colour=variant.colour,
                    quantity=quantity if quantity is not None else parse_quantity(description),
                    price=price,
                    # The line says '905 USD' and this was throwing it away, leaving the
                    # supplier's configured default to cover for text that was perfectly
                    # explicit. An offer with no currency cannot be compared against
                    # anything, and a supplier who normally quotes AED but wrote USD
                    # today meant USD.
                    currency=normalise_currency(variant.text),
                    # Read here as well as in the table path. Every incoterm in the sample
                    # corpus is in prose — '€437,90 EXW' at the end of a bullet — so
                    # setting it only on the table side captured none of them.
                    incoterm=detect_incoterm(variant.text),
                    price_basis=detect_price_basis(variant.text),
                    source_ref=f"body line {number}",
                )
            )

    return rows, expanded


def _quantity_from_markers(text: str) -> tuple[int | None, str]:
    """Pull the quantity out of '*288*' and tidy the markers out of the description."""
    quantity: int | None = None

    for match in _MARKER.finditer(text):
        inner = match.group(1)
        if _MARKER_IS_MONEY.search(inner):
            continue
        digits = inner.strip()
        if digits.isdigit() and 0 < int(digits) <= 100_000 and quantity is None:
            quantity = int(digits)

    cleaned = _MARKER.sub(" ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—")
    return quantity, cleaned or text
