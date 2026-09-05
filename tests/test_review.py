"""The review queue, and what resolving an email actually does.

Two claims are worth holding down here. Resolving must *reprocess* — a WhatsApp offer
attributed to the right supplier is worth nothing if their stock does not then appear on
the board. And an email id from one client's queue must not reach another client's, which
is the only thing standing between two tenants sharing an endpoint.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.dependencies import set_repository
from app.pipeline import process_email, reprocess_email
from app.schemas.email import Attachment, InboundEmail

TENANT = TenantConfig(id="tenant-1", name="TVD")
OTHER = TenantConfig(id="tenant-2", name="Someone else")

ROWS = [
    ["Description", "Qty", "Price"],
    ["Apple iPhone 15 128GB Black", "50", "579"],
    ["Apple iPhone 16 128GB Teal", "45", "679"],
    ["Apple iPhone 16 256GB Pink", "20", "779"],
]


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT, OTHER])


def _email(
    *,
    tenant="tenant-1",
    subject="Fwd: message",
    sender="sales@supplier.example",
    message_id="<m1@x>",
    body="pasted from whatsapp",
    rows=None,
):
    return InboundEmail(
        tenant_id=tenant,
        message_id=message_id,
        from_email=sender,
        subject=subject,
        received_at=datetime(2026, 8, 7, 9, 0),
        body_text=body,
        attachments=[Attachment(filename="stock.xlsx", kind="table", rows=rows or ROWS)],
    )


# ---------------------------------------------------------------- the queue


def test_an_unclassified_email_appears_in_the_queue(repo):
    """Without this it appeared nowhere at all. The design says an undecidable email
    costs a click; for a quarter of every batch there was no click available."""
    result = process_email(_email(), repo)
    assert result.outcome == "unclassified"

    queue = repo.list_review_queue("tenant-1")
    assert len(queue) == 1
    assert queue[0].reason == "classification"
    assert queue[0].needs_classification is True
    assert queue[0].attachment_count == 1


def test_a_filed_email_does_not_appear_in_the_queue(repo):
    process_email(_email(subject="WTS Price List 07.08"), repo)
    assert repo.list_review_queue("tenant-1") == []


def test_an_empty_queue_is_told_apart_from_an_empty_board(repo):
    """Both render as a blank page and they mean opposite things: one is every email
    having been filed without help, the other is no email having arrived at all."""
    assert repo.count_emails("tenant-1") == 0

    process_email(_email(subject="WTS Price List 07.08"), repo)

    assert repo.list_review_queue("tenant-1") == []
    assert repo.count_emails("tenant-1") == 1
    assert repo.count_emails("tenant-2") == 0


def test_the_queue_is_per_tenant(repo):
    process_email(_email(), repo)
    process_email(_email(tenant="tenant-2", message_id="<m2@x>"), repo)

    assert len(repo.list_review_queue("tenant-1")) == 1
    assert len(repo.list_review_queue("tenant-2")) == 1


# ---------------------------------------------------------------- resolving


def test_resolving_the_side_puts_the_stock_on_the_board(repo):
    """The point of the whole feature. Labelling the email and stopping would leave the
    supplier's list sitting in the database, correctly filed and entirely invisible."""
    process_email(_email(), repo)
    email_id = next(iter(repo.emails))

    assert repo.live_offers() == []

    repo.attribute_email("tenant-1", email_id, classification="sell")
    result = reprocess_email("tenant-1", email_id, repo)

    assert result.outcome == "processed"
    assert result.side == "sell"
    assert result.row_count == 3
    assert len(repo.live_offers()) == 3


def test_resolving_the_sender_creates_and_uses_the_counterparty(repo):
    """A retyped WhatsApp offer carries no supplier address anywhere in it."""
    result = process_email(_email(sender="", subject="WTS list"), repo)
    assert result.outcome == "needs_sender_review"

    email_id = next(iter(repo.emails))
    counterparty = repo.create_counterparty("tenant-1", "sales@vadimpex.example", "Vadimpex")
    repo.attribute_email("tenant-1", email_id, counterparty_id=counterparty.id)

    result = reprocess_email("tenant-1", email_id, repo)

    assert result.outcome == "processed"
    assert result.counterparty_id == counterparty.id
    assert len(repo.live_offers()) == 3


def test_attributing_records_that_a_person_did_it(repo):
    """The next person to look should be able to tell a resolved address from a guessed
    one. 'manual' is a real value in public.sender_method for exactly this."""
    process_email(_email(sender="", subject="WTS list"), repo)
    email_id = next(iter(repo.emails))
    counterparty = repo.create_counterparty("tenant-1", "sales@vadimpex.example", None)

    repo.attribute_email("tenant-1", email_id, counterparty_id=counterparty.id)

    stored = repo.emails[email_id]
    assert stored.counterparty_method == "manual"
    assert stored.counterparty_confidence == 1.0
    assert stored.needs_sender_review is False


def test_an_email_belonging_to_another_tenant_cannot_be_resolved(repo):
    """The tenant filter is the security boundary, not a nicety."""
    process_email(_email(), repo)
    email_id = next(iter(repo.emails))

    assert repo.attribute_email("tenant-2", email_id, classification="sell") is False
    assert repo.get_stored_email("tenant-2", email_id) is None
    assert repo.emails[email_id].classification == "unclassified"


def test_reprocessing_does_not_duplicate_the_email(repo):
    """The email row is not written again, so the deduplication guard stays honest. A
    second copy would make the next genuine delivery look like a redelivery."""
    process_email(_email(), repo)
    email_id = next(iter(repo.emails))

    repo.attribute_email("tenant-1", email_id, classification="sell")
    reprocess_email("tenant-1", email_id, repo)

    assert len(repo.emails) == 1


def test_reprocessing_twice_does_not_double_the_board(repo):
    """Somebody will click it twice. The second pass sees its own rows as already live
    and refreshes them rather than inserting a parallel set."""
    process_email(_email(), repo)
    email_id = next(iter(repo.emails))
    repo.attribute_email("tenant-1", email_id, classification="sell")

    reprocess_email("tenant-1", email_id, repo)
    second = reprocess_email("tenant-1", email_id, repo)

    assert len(repo.live_offers()) == 3
    assert second.reconcile is not None
    assert second.reconcile.inserted == 0
    assert second.reconcile.closed == 0


def test_a_still_unresolved_email_reconciles_nothing(repo):
    """Reprocessing an email that is still missing its side must not invent one."""
    process_email(_email(), repo)
    email_id = next(iter(repo.emails))

    result = reprocess_email("tenant-1", email_id, repo)

    assert result.outcome == "unclassified"
    assert repo.live_offers() == []


def test_reprocessing_an_email_that_does_not_exist(repo):
    assert reprocess_email("tenant-1", "nope", repo).outcome == "not_found"


# ---------------------------------------------------------------- the endpoints


def test_the_queue_requires_a_token(client):
    set_repository(InMemoryRepository([TENANT]))
    try:
        assert client.get("/review/queue").status_code == 401
    finally:
        set_repository(None)


def test_resolving_requires_a_token(client):
    set_repository(InMemoryRepository([TENANT]))
    try:
        response = client.post("/review/resolve", json={"email_id": "x", "side": "sell"})
        assert response.status_code == 401
    finally:
        set_repository(None)


def test_authentication_is_checked_before_the_body(client):
    """An anonymous caller gets 401 whatever they sent. Validating the body first would
    tell someone with no session which payloads are well-formed."""
    assert client.post("/review/resolve", json={}).status_code == 401


def test_the_tenant_cannot_be_passed_in(client):
    """A tenant id in the request is a request to work on someone else's board. Neither
    endpoint takes one, and adding one must stay a deliberate act."""
    import inspect

    from app.api.review import ResolveIn, queue, resolve

    for endpoint in (queue, resolve):
        assert "tenant_id" not in inspect.signature(endpoint).parameters
    assert "tenant_id" not in ResolveIn.model_fields


def test_resolving_needs_something_to_do():
    """An empty resolution would relabel nothing and reprocess anyway."""
    from pydantic import ValidationError

    from app.api.review import ResolveIn

    with pytest.raises(ValidationError):
        ResolveIn(email_id="x")

    with pytest.raises(ValidationError):
        ResolveIn(email_id="x", side="maybe")
