"""A second list from one supplier on one day.

The behaviour these protect is the one that destroys data when it is wrong. A supplier
sends the morning's stock, sells three lots, and sends again at four — and read as a
complete list, that afternoon email closes every row it omits. Half the time it is not a
complete list at all, but 'here are the 40 I forgot this morning', and reconciling it
closes the other 450.

So the first test is the one to read: the same email sequence that used to close a
supplier's board now changes nothing until a person says so.
"""

from datetime import datetime, timedelta

import pytest

from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.pipeline import process_email
from app.schemas.email import Attachment, InboundEmail

TENANT = TenantConfig(
    id="tenant-1",
    name="TVD",
    internal_domains={"tvdservices.com"},
    timezone="Europe/Amsterdam",
)

HEADERS = ["Description", "Qty", "Price"]

MORNING = [
    HEADERS,
    ["Apple iPhone 16 128GB Black", "50", "679"],
    ["Apple iPhone 16 256GB Teal", "45", "749"],
    ["Samsung S25 512GB Mint", "18", "1045"],
    ["Google Pixel 10 128GB Obsidian", "30", "549"],
]

# The dangerous email: three rows, not four, and not a complete list. Reconciled, it
# closes the Pixel.
AFTERNOON_PARTIAL = [
    HEADERS,
    ["Apple iPhone 16 128GB Black", "40", "679"],
    ["Apple iPhone 16 256GB Teal", "45", "749"],
    ["Samsung S25 512GB Mint", "18", "1045"],
]


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT])


def email(rows, *, message_id, at, subject="WTS stock list"):
    return InboundEmail(
        tenant_id="tenant-1",
        message_id=message_id,
        from_email="sales@supplier.example",
        subject=subject,
        received_at=at,
        body_text="Please find our stock list.",
        attachments=[Attachment(filename="stock.xlsx", kind="table", rows=rows)],
    )


def live_keys(repo):
    return {o.identity_key for o in repo.live_offers()}


# ---------------------------------------------------------------- the destructive case


def test_a_second_list_today_does_not_close_the_first_lists_rows(repo):
    """The whole reason this exists.

    Before the same-day rule, the afternoon email was reconciled as a complete list and
    the Pixel — absent from it, because the supplier only re-sent part of their stock —
    was closed as sold. Real stock, off the board, with nobody told.
    """
    first = process_email(
        email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo
    )
    assert first.outcome == "processed"
    before = live_keys(repo)
    assert len(before) == 4

    second = process_email(
        email(AFTERNOON_PARTIAL, message_id="<m2@x>", at=datetime(2026, 10, 16, 16, 0)),
        repo,
    )

    assert second.outcome == "held_for_review"
    assert live_keys(repo) == before, "the board moved while a decision was pending"
    assert all(o.status == "live" for o in repo.live_offers())


def test_the_held_rows_are_kept_for_the_person_deciding(repo):
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)
    result = process_email(
        email(AFTERNOON_PARTIAL, message_id="<m2@x>", at=datetime(2026, 10, 16, 16, 0)),
        repo,
    )

    held = [rows for rows in repo.held_rows.values()]
    assert len(held) == 1
    assert len(held[0]) == 3
    assert result.row_count == 3

    # Each row already knows what it would do, so the review screen does not have to
    # work it out on every open.
    actions = {row["action"] for row in held[0]}
    assert actions <= {"insert", "update", "refresh"}
    # The iPhone 128GB dropped from 50 to 40, so it is an update carrying the old value.
    changed = [r for r in held[0] if r["action"] == "update"]
    assert changed and changed[0]["previous_quantity"] == 50


# ---------------------------------------------------------------- the identical case


def test_an_unchanged_resend_is_decided_without_a_person(repo):
    """Also what a duplicate delivery looks like: one supplier email addressed to two
    staff, forwarded by two collectors, where Outlook wrapped the second copy and gave
    it a new Message-ID so message-id dedupe never saw it."""
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)
    before = live_keys(repo)

    again = process_email(
        email(MORNING, message_id="<wrapped-copy@outlook>", at=datetime(2026, 10, 16, 11, 0)),
        repo,
    )

    assert again.outcome == "same_as_applied"
    assert live_keys(repo) == before
    assert repo.held_rows == {}, "nothing to decide, so nothing should be queued"


def test_an_unchanged_resend_still_counts_as_seen_today(repo):
    """Prices and quantities are untouched, but the supplier has just confirmed this
    stock is current. Letting it age would drop live stock off a board sorted by
    freshness for no reason."""
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)
    for offer in repo.offers.values():
        offer.last_confirmed_at = datetime(2026, 10, 16, 9, 0)

    process_email(email(MORNING, message_id="<m2@x>", at=datetime(2026, 10, 16, 11, 0)), repo)

    assert all(
        o.last_confirmed_at > datetime(2026, 10, 16, 9, 0) for o in repo.live_offers()
    )


# ---------------------------------------------------------------- the day boundary


def test_the_next_days_list_applies_normally(repo):
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)

    tomorrow = process_email(
        email(AFTERNOON_PARTIAL, message_id="<m2@x>", at=datetime(2026, 10, 17, 9, 0)),
        repo,
    )

    assert tomorrow.outcome == "processed"


def test_a_punctual_supplier_is_not_held_every_morning(repo):
    """Why this is a calendar day and not a rolling 24 hours.

    A supplier who sends at 09:00 Monday and 08:30 Tuesday is 23.5 hours apart. Under a
    rolling window Tuesday's perfectly ordinary list is held for review — every day, for
    every punctual supplier, until the queue is noise and nobody opens it.
    """
    monday = datetime(2026, 10, 12, 7, 0)       # 09:00 in Amsterdam
    tuesday = datetime(2026, 10, 13, 6, 30)     # 08:30 in Amsterdam, 23.5h later
    assert tuesday - monday < timedelta(hours=24)

    process_email(email(MORNING, message_id="<mon@x>", at=monday), repo)
    result = process_email(email(AFTERNOON_PARTIAL, message_id="<tue@x>", at=tuesday), repo)

    assert result.outcome == "processed"


def test_the_day_is_the_clients_day_not_the_suppliers(repo):
    """22:30 UTC is already tomorrow in Amsterdam. A tenant in another zone would read
    the same two emails as one day's, and that is the point of storing the zone."""
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 20, 0)), repo)

    # 22:30 UTC = 00:30 on the 17th in Amsterdam, so this is a new day and applies.
    result = process_email(
        email(AFTERNOON_PARTIAL, message_id="<m2@x>", at=datetime(2026, 10, 16, 22, 30)),
        repo,
    )
    assert result.outcome == "processed"


def test_the_day_is_judged_by_the_emails_clock_not_the_rows(repo):
    """The bug this is named after passed for eleven hours and failed at midnight.

    An arriving email was judged by its `received_at` and the import it was compared
    against by its `created_at` — when the row was WRITTEN. Those are different clocks.
    An email that arrives at 23:55 and is processed at 00:10, because n8n polls on an
    interval or is catching up after an outage, gets recorded as the next day's list,
    and the revision that follows it is applied straight to the board instead of being
    held.

    Simulated here by writing the first import with a `created_at` far from the email
    it came from, which is exactly what an ingestion delay looks like.
    """
    # 18:00 and 20:00 UTC are 20:00 and 22:00 in Amsterdam — the same evening, both
    # safely short of midnight there, so nothing but the write time can separate them.
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 18, 0)), repo)

    # The row was written two days later: an extreme backlog, but the direction of the
    # error is what matters, not its size.
    repo.imports[0].created_at = datetime(2026, 10, 18, 9, 0)

    result = process_email(
        email(AFTERNOON_PARTIAL, message_id="<m2@x>", at=datetime(2026, 10, 16, 20, 0)),
        repo,
    )

    assert result.outcome == "held_for_review", (
        "both emails arrived the same evening; only the row's write time says otherwise"
    )


def test_an_import_records_when_its_email_arrived(repo):
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)

    assert repo.imports[0].record.source_received_at == datetime(2026, 10, 16, 9, 0)


# ---------------------------------------------------------------- scope


def test_a_buying_list_does_not_hold_a_stock_list(repo):
    """A supplier who sends their stock in the morning and what they want at noon has
    resent nothing. Holding the second would be a queue entry nobody needed."""
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)

    wtb = InboundEmail(
        tenant_id="tenant-1",
        message_id="<m2@x>",
        from_email="sales@supplier.example",
        subject="WTB urgently",
        received_at=datetime(2026, 10, 16, 12, 0),
        body_text="Looking to buy, please quote:\n\nApple iPhone 15 128GB — 200pcs",
        attachments=[],
    )
    result = process_email(wtb, repo)

    assert result.side == "buy"
    assert result.outcome != "held_for_review"


def test_a_held_list_does_not_become_what_the_next_one_is_compared_against(repo):
    """A held import changed nothing about the board. If it could stand in as the prior,
    the third list of a day would be compared against rows nobody ever approved — and a
    second list that happened to match the first held one would be waved through."""
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)
    process_email(
        email(AFTERNOON_PARTIAL, message_id="<m2@x>", at=datetime(2026, 10, 16, 14, 0)), repo
    )

    third = process_email(
        email(AFTERNOON_PARTIAL, message_id="<m3@x>", at=datetime(2026, 10, 16, 17, 0)), repo
    )

    # Identical to the HELD list, but that one is not on the board — so this is still a
    # list that differs from what the board holds, and still needs a person.
    assert third.outcome == "held_for_review"


def test_a_different_supplier_is_untouched_by_the_rule(repo):
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)

    other = email(MORNING, message_id="<m2@x>", at=datetime(2026, 10, 16, 10, 0))
    other = other.model_copy(update={"from_email": "sales@another.example"})

    assert process_email(other, repo).outcome == "processed"


# ---------------------------------------------------------------- the import record


def test_the_import_log_says_what_happened_and_why(repo):
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)
    process_email(
        email(AFTERNOON_PARTIAL, message_id="<m2@x>", at=datetime(2026, 10, 16, 16, 0)), repo
    )

    held = repo.imports[-1].record
    assert held.status == "held_for_review"
    assert held.offers_inserted == held.offers_updated == held.offers_closed == 0
    assert held.side == "sell"
    assert held.content_hash
    assert held.compared_to_import_id == repo.imports[0].id
    assert held.difference_summary and held.difference_summary != "identical"


def test_an_applied_import_records_what_the_list_said(repo):
    """Without the hash on the applied import, the next same-day list has nothing to
    compare against and is held even when it is an exact resend."""
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)

    first = repo.imports[0].record
    assert first.status == "applied"
    assert first.content_hash
    assert first.side == "sell"
