"""The whole brain, end to end, against a working in-memory store.

These are the tests that matter most, because they check the state the offers actually
end up in rather than the calls that were made along the way. Reconciliation's failure
mode is silent: no exception, no error log, just stock that quietly disappeared.
"""

from datetime import datetime

import pytest

from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.pipeline import process_email
from app.schemas.email import Attachment, InboundEmail

TENANT = TenantConfig(
    id="tenant-1",
    name="TVD",
    internal_domains={"tvdservices.com"},
    internal_addresses={"tvdlogistics@outlook.com"},
)

HEADERS = ["Description", "EAN", "Qty", "Price"]
DAY_ONE = [
    HEADERS,
    ["Apple iPhone 15 128GB Black", "0195949035999", "50", "579"],
    ["Apple iPhone 16 128GB Teal", "0195949036002", "45", "679"],
    ["Apple USB-C Power Adapter", "0195949121296", "79", "11.90"],
]


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT])


def email(rows=None, *, subject="WTS Price List 07.08.2026", message_id="<m1@x>",
          sender="sales@supplier.example", body="Please find our price list."):
    return InboundEmail(
        tenant_id="tenant-1",
        message_id=message_id,
        from_email=sender,
        subject=subject,
        received_at=datetime(2026, 8, 7, 9, 0),
        body_text=body,
        attachments=[
            Attachment(filename="stock.xlsx", kind="table", rows=rows or DAY_ONE)
        ],
    )


# ---------------------------------------------------------------- happy path


def test_first_import_creates_counterparty_and_offers(repo):
    result = process_email(email(), repo)

    assert result.processed
    assert result.side == "sell"
    assert result.reconcile.inserted == 3
    assert len(repo.live_offers()) == 3
    assert repo.counterparties


def test_second_identical_import_changes_nothing(repo):
    process_email(email(), repo)
    result = process_email(email(message_id="<m2@x>"), repo)

    assert result.reconcile.refreshed == 3
    assert result.reconcile.updated == 0
    assert result.reconcile.closed == 0
    assert len(repo.live_offers()) == 3


def test_a_normal_second_morning(repo):
    """One repriced, one sold overnight, one new."""
    process_email(email(), repo)

    day_two = [
        HEADERS,
        ["Apple iPhone 15 128GB Black", "0195949035999", "50", "579"],
        ["Apple iPhone 16 128GB Teal", "0195949036002", "30", "665"],
        ["Apple iPhone 17 256GB White", "0195950643701", "29", "749"],
    ]
    result = process_email(email(day_two, message_id="<m2@x>"), repo)

    counts = (
        result.reconcile.refreshed,
        result.reconcile.updated,
        result.reconcile.inserted,
        result.reconcile.closed,
    )
    assert counts == (1, 1, 1, 1)

    live = {o.description for o in repo.live_offers()}
    assert "Apple USB-C Power Adapter" not in live
    assert "Apple iPhone 17 256GB White" in live


def test_price_change_is_written_to_the_change_log(repo):
    """The client has to be able to ask why a price moved and get an answer."""
    process_email(email(), repo)
    day_two = [HEADERS, ["Apple iPhone 15 128GB Black", "0195949035999", "50", "545"]]
    process_email(email(day_two, message_id="<m2@x>", subject="WTS list"), repo)

    price_changes = [c for c in repo.changes if c.field == "unit_price"]
    assert price_changes
    assert price_changes[0].old == "579.0"
    assert price_changes[0].new == "545.0"
    assert price_changes[0].email_id is not None


# ---------------------------------------------------------------- the guards


def test_redelivery_of_the_same_message_is_ignored(repo):
    """Mailbox re-syncs redeliver messages. Reconciling twice would let the second pass
    close anything missing from the redelivered copy."""
    process_email(email(), repo)
    result = process_email(email(), repo)

    assert result.outcome == "duplicate"
    assert len(repo.live_offers()) == 3


def test_a_broken_parse_closes_nothing(repo):
    """The failure that matters: yesterday's list yielded 3 rows, today's attachment is
    malformed and yields 1."""
    process_email(email(), repo)

    broken = [HEADERS, ["Apple iPhone 15 128GB Black", "0195949035999", "50", "579"]]
    result = process_email(email(broken, message_id="<m2@x>"), repo)

    assert result.reconcile.status == "flagged_low_row_count"
    assert result.reconcile.closed == 0
    assert len(repo.live_offers()) == 3


def test_a_supplement_closes_nothing(repo):
    """'Just got 50 more' is not a complete list, and its absences prove nothing."""
    process_email(email(), repo)

    extra = [HEADERS, ["Apple iPhone 17 256GB White", "0195950643701", "50", "749"]]
    result = process_email(
        email(extra, message_id="<m2@x>", subject="Just got 50 more iPhone 17"), repo
    )

    assert result.reconcile.closed == 0
    assert len(repo.live_offers()) == 4


def test_unresolvable_sender_is_stored_but_not_reconciled(repo):
    """A WhatsApp offer retyped by staff carries no supplier address anywhere."""
    payload = email(sender="sales@tvdservices.com", body="Offer below, no addresses.")
    result = process_email(payload, repo)

    assert result.outcome == "needs_sender_review"
    assert result.email_id is not None
    assert repo.live_offers() == []
    assert repo.emails[result.email_id].needs_sender_review is True


def test_ambiguous_side_is_not_guessed(repo):
    """A price list filed as buyer demand would have the client chasing people to sell
    them stock those people are themselves selling."""
    result = process_email(email(subject="Hello", body="See attached."), repo)

    assert result.outcome == "unclassified"
    assert repo.live_offers() == []


def test_buyer_requests_are_kept_apart_from_seller_offers(repo):
    """Same products, opposite sides. They must never reconcile against each other."""
    process_email(email(), repo)
    result = process_email(
        email(message_id="<m2@x>", subject="WTB Action 07.08.2026"), repo
    )

    assert result.side == "buy"
    assert result.reconcile.inserted == 3
    assert result.reconcile.closed == 0
    assert len(repo.live_offers()) == 6


# ---------------------------------------------------------------- extraction


def test_prose_offers_are_read_when_there_is_no_table():
    repo = InMemoryRepository([TENANT])
    body = (
        "WTS today\n"
        "• A17 LTE DS SM-A175 4+128 — Black / Blue / Grey — €125\n"
        "• S25 Ultra S938B 5G DS 12+256 — Titanium Grey — €674\n"
    )
    payload = InboundEmail(
        tenant_id="tenant-1",
        message_id="<prose@x>",
        from_email="sales@supplier.example",
        subject="WTS stock list",
        received_at=datetime(2026, 8, 7, 9, 0),
        body_text=body,
    )

    result = process_email(payload, repo)

    # Three colours plus one single-colour line.
    assert result.row_count == 4
    assert result.expanded_rows == 2
    assert len(repo.live_offers()) == 4


def test_european_prices_use_the_suppliers_setting(repo):
    rows = [HEADERS, ["4smarts Pico Dual 20W Car Charger", "4252011907762", "3", "2,50"]]
    process_email(email(rows), repo)

    offer = repo.live_offers()[0]
    assert offer.unit_price == 2.50


def test_import_is_recorded_for_the_audit_trail(repo):
    process_email(email(), repo)

    record = repo.imports[0]
    assert record.row_count == 3
    assert record.offers_inserted == 3
    assert record.status == "applied"
    assert "completeness" in record.notes


def test_unknown_tenant_is_refused(repo):
    payload = email()
    payload = payload.model_copy(update={"tenant_id": "nope"})
    assert process_email(payload, repo).outcome == "unknown_tenant"
