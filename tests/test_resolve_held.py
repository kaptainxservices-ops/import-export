"""Settling a list that was held back.

The three answers differ in one thing: whether absence is allowed to mean 'sold'. That
is the only inference in this system that destroys data, so each test here is really
asking the same question — did this answer close anything, and was it entitled to?
"""

from datetime import datetime

import pytest

from app.brain.listdiff import compare
from app.brain.reconcile import ExistingOffer
from app.brain.resolve import settle
from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.pipeline import process_email
from app.schemas.email import Attachment, InboundEmail

TENANT = TenantConfig(
    id="tenant-1", name="TVD", internal_domains={"tvdservices.com"},
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
# Pixel absent; the iPhone repriced; a Motorola that was never on the board.
AFTERNOON = [
    HEADERS,
    ["Apple iPhone 16 128GB Black", "50", "655"],
    ["Apple iPhone 16 256GB Teal", "45", "749"],
    ["Samsung S25 512GB Mint", "12", "1045"],
    ["Motorola Edge 70 Pro 5G 8/256GB Titan", "80", "319"],
]


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT])


def email(rows, *, message_id, at):
    return InboundEmail(
        tenant_id="tenant-1", message_id=message_id,
        from_email="sales@supplier.example", subject="WTS stock list",
        received_at=at, body_text="Our stock list.",
        attachments=[Attachment(filename="stock.xlsx", kind="table", rows=rows)],
    )


@pytest.fixture
def held(repo):
    """A board with the morning list on it, and an afternoon list waiting."""
    process_email(email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)
    process_email(email(AFTERNOON, message_id="<m2@x>", at=datetime(2026, 10, 16, 16, 0)), repo)
    import_id = repo.imports[-1].id
    assert repo.imports[-1].record.status == "held_for_review"
    return import_id


def board(repo):
    return {o.identity_key: o for o in repo.live_offers()}


# ---------------------------------------------------------------- the diff


def test_the_diff_separates_what_a_person_must_look_at(repo, held):
    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")

    class R:
        def __init__(self, row):
            self.identity_key = row["identity_key"]
            self.quantity = row.get("quantity")
            self.unit_price = row.get("unit_price")
            self.currency = row.get("currency")
            self.description = row.get("description") or ""

    diff = compare(live, [R(row) for row in record["rows"]])

    assert diff.counts["appeared"] == 1      # the Motorola
    assert diff.counts["vanished"] == 1      # the Pixel
    assert diff.counts["price"] == 1         # the iPhone 128
    assert diff.counts["quantity"] == 1      # the Samsung
    assert diff.counts["unchanged"] == 1     # the iPhone 256

    # Sorted so the screen reads top-down in order of how much it matters.
    assert [c.kind for c in diff.changes][:2] == ["appeared", "vanished"]


def test_a_price_move_is_reported_as_a_fraction(repo, held):
    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")
    rows = [type("R", (), dict(
        identity_key=r["identity_key"], quantity=r.get("quantity"),
        unit_price=r.get("unit_price"), currency=r.get("currency"),
        description=r.get("description") or "",
    ))() for r in record["rows"]]

    moved = compare(live, rows).of("price")[0]

    # 679 -> 655 is a touch over 3% off; a trader reads that differently from 40%.
    assert moved.price_move is not None
    assert -0.05 < moved.price_move < 0


# ---------------------------------------------------------------- same


def test_same_changes_no_prices_and_closes_nothing(repo, held):
    before = {k: (o.quantity, o.unit_price) for k, o in board(repo).items()}

    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")
    outcome = settle("same", held=record["rows"], live=live)

    assert outcome.actions == []
    assert outcome.refresh_keys
    after = {k: (o.quantity, o.unit_price) for k, o in board(repo).items()}
    assert after == before


def test_same_does_not_confirm_a_row_the_board_never_had(repo, held):
    """The Motorola is on the held list and not on the board. 'Nothing here is news'
    is not the answer that puts a new product on the board."""
    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")

    outcome = settle("same", held=record["rows"], live=live)

    on_board = {o.identity_key for o in live}
    assert set(outcome.refresh_keys) <= on_board
    assert len(outcome.refresh_keys) == 3


# ---------------------------------------------------------------- new


def test_new_applies_everything_and_may_close(repo, held):
    """'This is their current stock' is the statement reconciliation needs, and a
    person made it deliberately — the one circumstance where closure is licensed."""
    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")

    outcome = settle("new", held=record["rows"], live=live)
    kinds = [action.kind for action in outcome.actions]

    assert outcome.may_close is True
    assert "insert" in kinds        # the Motorola
    assert "close" in kinds         # the Pixel, absent from a complete list
    assert "update" in kinds        # the repriced iPhone


# ---------------------------------------------------------------- partial


def test_partial_never_closes_anything(repo, held):
    """The test this module exists for.

    Someone ticking two rows out of five is vouching for two rows. The other three were
    not examined, and reading their absence from a *selection* as 'sold' would turn a
    careful narrow action into the widest possible one.
    """
    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")
    chosen = [record["rows"][0]["identity_key"]]

    outcome = settle("partial", held=record["rows"], live=live, chosen_keys=chosen)

    assert outcome.may_close is False
    assert [action.kind for action in outcome.actions] == ["update"]
    assert not any(action.kind == "close" for action in outcome.actions)


def test_partial_applies_only_what_was_ticked(repo, held):
    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")
    motorola = next(
        row["identity_key"] for row in record["rows"] if "otorola" in (row["description"] or "")
    )

    outcome = settle("partial", held=record["rows"], live=live, chosen_keys=[motorola])

    assert len(outcome.actions) == 1
    assert outcome.actions[0].kind == "insert"
    assert outcome.actions[0].identity_key == motorola


def test_partial_ignores_a_key_that_is_not_in_the_list(repo, held):
    """The screen may be a version behind. Inventing a row from a stale selection is
    worse than ignoring it."""
    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")

    outcome = settle(
        "partial", held=record["rows"], live=live, chosen_keys=["spec:ghost||||"]
    )

    assert outcome.actions == []


def test_partial_with_nothing_ticked_does_nothing(repo, held):
    record = repo.get_held_import("tenant-1", held)
    live = repo.load_live_offers("tenant-1", record["counterparty_id"], "sell")

    outcome = settle("partial", held=record["rows"], live=live, chosen_keys=[])

    assert outcome.actions == []
    assert "nothing applied" in outcome.note


# ---------------------------------------------------------------- bookkeeping


def test_a_list_can_only_be_settled_once(repo, held):
    """Two people on the same queue, or one person clicking twice on a slow
    connection, must not apply a supplier's list to the board twice."""
    assert repo.mark_import_resolved("tenant-1", held, "same", "user-1") is True
    assert repo.mark_import_resolved("tenant-1", held, "new", "user-2") is False


def test_a_settled_list_leaves_the_queue(repo, held):
    assert len(repo.list_held_imports("tenant-1")) == 1

    repo.mark_import_resolved("tenant-1", held, "same", "user-1")

    assert repo.list_held_imports("tenant-1") == []


def test_an_unknown_resolution_is_refused(repo, held):
    record = repo.get_held_import("tenant-1", held)
    with pytest.raises(ValueError):
        settle("maybe", held=record["rows"], live=[])


def test_settle_reads_held_rows_without_a_board(repo, held):
    """A supplier whose every row is new — the board has nothing to compare against."""
    record = repo.get_held_import("tenant-1", held)

    outcome = settle("new", held=record["rows"], live=[])

    assert all(action.kind == "insert" for action in outcome.actions)
    assert len(outcome.actions) == 4


def test_vanished_rows_are_shown_but_not_acted_on(repo, held):
    """The part of the question a person is least able to answer from memory."""
    record = repo.get_held_import("tenant-1", held)
    live = [
        ExistingOffer(id="o1", identity_key="spec:gone||||", quantity=5, unit_price=100.0)
    ]

    diff = compare(live, [])
    assert diff.of("vanished")[0].identity_key == "spec:gone||||"

    outcome = settle("partial", held=record["rows"], live=live, chosen_keys=[])
    assert outcome.actions == []
