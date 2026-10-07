"""Review at the level of the list — the half of the review screen that did not exist.

The frontend has called these five endpoints since August. The backend had two. So the
Review tab returned a raw 404 in production, and because `ReviewQueue` renders
`ImportQueue` inside itself, the broken half took the working half down with it.

That matters more now than it did then. With the collector mailbox, about one email in
five arrives with no supplier address anywhere — offers a staff member retyped, where
the company name exists only in the subject. Those land here. Until this screen works, a
fifth of the client's inbound mail has nowhere to go.

The test that earns its place is `test_rejecting_puts_back_what_it_displaced`. Withdrawing
the rows a bad import added is the obvious half; restoring the rows it closed as sold is
the half that is easy to forget, and forgetting it means a rejected bad parse still
destroys real stock.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_caller, current_tenant
from app.db.memory import InMemoryRepository, StoredOffer
from app.db.models import TenantConfig
from app.dependencies import set_repository
from app.pipeline import process_email
from app.schemas.email import Attachment, InboundEmail

TENANT = TenantConfig(id="tenant-1", name="TVD")
OTHER = TenantConfig(id="tenant-2", name="Someone else")

ROWS = [
    ["Description", "Qty", "Price"],
    ["Apple iPhone 15 128GB Black", "50", "579"],
    ["Apple iPhone 16 128GB Teal", "45", "679"],
    ["Apple iPhone 16 256GB Pink", "20", "779"],
]

# For the closure case. Reconciliation will not close anything off a list that has
# collapsed in size, so demonstrating a closure needs a list long enough that losing
# one lot reads as a sale rather than as a bad parse.
BIG_ROWS = [["Description", "Qty", "Price"]] + [
    [f"Apple iPhone 1{n} 128GB Black", "50", str(500 + n)] for n in range(10)
]


@pytest.fixture
def repo():
    store = InMemoryRepository([TENANT, OTHER])
    set_repository(store)
    yield store
    set_repository(None)


@pytest.fixture
def client(repo) -> TestClient:
    from app.main import app

    app.dependency_overrides[current_tenant] = lambda: ("tenant-1", repo)
    app.dependency_overrides[current_caller] = lambda: ("tenant-1", "user-1", repo)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _email(*, message_id="<m1@x>", rows=None, tenant="tenant-1"):
    return InboundEmail(
        tenant_id=tenant,
        message_id=message_id,
        from_email="sales@supplier.example",
        subject="WTS Price List 07.08.2026",
        received_at=datetime(2026, 8, 7, 9, 0),
        body_text="Our list attached.",
        attachments=[Attachment(filename="stock.xlsx", kind="table", rows=rows or ROWS)],
    )


# ---------------------------------------------------------------- the list


def test_an_import_waits_for_a_verdict(client, repo):
    process_email(_email(), repo)

    body = client.get("/review/imports").json()

    assert body["waiting"] == 1
    item = body["items"][0]
    assert item["rows_parsed"] == 3
    assert item["subject"] == "WTS Price List 07.08.2026"
    assert item["verdict"] in {"healthy", "check", "suspect"}
    assert item["verdict_reason"]


def test_the_verdict_explains_itself(client, repo):
    """A queue that says 'flagged' and nothing else gets approved blindly.

    These rows carry bare numbers with no currency anywhere in the list, so the verdict
    is 'check' rather than 'healthy' — correctly. That is a fact about the list, not
    about any row, which is why it belongs on the card and not against 3 line items.
    """
    process_email(_email(), repo)

    item = client.get("/review/imports").json()["items"][0]

    assert item["verdict"] == "check"
    assert "3 rows" in item["verdict_reason"]
    assert item["parse_warnings"]


def test_a_list_with_nothing_wrong_reads_as_healthy():
    from app.brain.verdict import judge_import

    verdict = judge_import(row_count=487, flagged_rows=0, previous_row_count=490)

    assert verdict.level == "healthy"
    assert "487" in verdict.reason


def test_approving_takes_it_out_of_the_queue(client, repo):
    process_email(_email(), repo)
    import_id = client.get("/review/imports").json()["items"][0]["id"]

    assert client.post(f"/review/imports/{import_id}/approve").json()["approved"] is True
    assert client.get("/review/imports").json()["waiting"] == 0


def test_approving_does_not_publish_the_flagged_rows(client, repo):
    """'Approve 475' and 'review the 12' are two decisions.

    Collapsing them would let one click push every doubtful row onto the board, which
    is the exact outcome the queue exists to prevent.
    """
    process_email(_email(), repo)
    offer = next(iter(repo.offers.values()))
    offer.needs_review = True
    offer.review_reason = "no price on this line"

    import_id = client.get("/review/imports").json()["items"][0]["id"]
    client.post(f"/review/imports/{import_id}/approve")

    assert repo.offers[offer.id].needs_review is True


def test_rejecting_withdraws_the_rows_it_added(client, repo):
    process_email(_email(), repo)
    import_id = client.get("/review/imports").json()["items"][0]["id"]

    body = client.post(f"/review/imports/{import_id}/reject").json()

    assert body["rejected"] is True
    assert body["withdrawn"] == 3
    assert all(o.status == "withdrawn" for o in repo.offers.values())


def test_rejecting_puts_back_what_it_displaced(client, repo):
    """The half that is easy to forget.

    A bad parse does not merely add wrong rows — it closes the supplier's previous list
    as sold. Reject it without reopening those and the bad parse has destroyed real
    stock even after being rejected.
    """
    # Monday: a full list. Ten rows rather than three, because reconciliation refuses
    # to close anything when the row count collapses — a 3-row list following a 10-row
    # one would be caught by that guard and close nothing, which is the guard working
    # and not the case under test here.
    process_email(_email(message_id="<mon@x>", rows=BIG_ROWS), repo)
    monday = {o.id for o in repo.offers.values()}

    # Tuesday: the same list minus one lot, small enough a drop to be believed.
    process_email(_email(message_id="<tue@x>", rows=BIG_ROWS[:-1]), repo)
    closed = [o for o in repo.offers.values() if o.id in monday and o.status == "sold"]
    assert closed, "the second import should have closed the row missing from it"

    waiting = client.get("/review/imports").json()["items"]
    tuesday_id = waiting[0]["id"]

    body = client.post(f"/review/imports/{tuesday_id}/reject").json()

    assert body["reopened"] == len(closed)
    for offer in closed:
        assert repo.offers[offer.id].status == "live"


def test_another_tenants_import_is_not_reachable(client, repo):
    process_email(_email(tenant="tenant-2", message_id="<theirs@x>"), repo)
    theirs = repo.imports[0].id

    assert client.post(f"/review/imports/{theirs}/approve").status_code == 404
    assert client.post(f"/review/imports/{theirs}/reject").status_code == 404
    assert client.get("/review/imports").json()["waiting"] == 0


# ---------------------------------------------------------------- the rows


def _flagged(repo, *, tenant_id="tenant-1", **overrides) -> StoredOffer:
    offer = StoredOffer(
        id="offer-flagged",
        tenant_id=tenant_id,
        counterparty_id="cp-1",
        side="sell",
        identity_key="spec:apple|iphone 15|128|black",
        description="Apple iPhone 15 128GB Black",
        quantity=None,
        unit_price=None,
        currency=None,
        needs_review=True,
        review_reason="no quantity and no price on this line",
        source_ref="row 7",
        **overrides,
    )
    repo.offers[offer.id] = offer
    return offer


def test_flagged_rows_say_what_is_wrong_with_them(client, repo):
    _flagged(repo)

    rows = client.get("/review/rows").json()

    assert len(rows) == 1
    assert rows[0]["review_reason"] == "no quantity and no price on this line"
    assert rows[0]["source_ref"] == "row 7"


def test_a_clean_row_is_not_in_the_queue(client, repo):
    process_email(_email(), repo)
    assert client.get("/review/rows").json() == []


def test_fixing_a_row_publishes_it(client, repo):
    offer = _flagged(repo)

    body = client.patch(
        f"/review/rows/{offer.id}", json={"quantity": 40, "unit_price": 579.0}
    ).json()

    assert body["fixed"] is True
    fixed = repo.offers[offer.id]
    assert fixed.quantity == 40
    assert fixed.unit_price == 579.0
    # Not still flagged: a person who fixes the same row twice stops trusting the queue.
    assert fixed.needs_review is False
    assert fixed.review_reason is None


def test_one_field_is_enough(client, repo):
    """A row flagged for a missing price is fixed by supplying a price.

    Making the person retype the description to do it is how a correction introduces a
    second error.
    """
    offer = _flagged(repo)

    client.patch(f"/review/rows/{offer.id}", json={"unit_price": 579.0})

    assert repo.offers[offer.id].description == "Apple iPhone 15 128GB Black"


def test_an_empty_correction_is_refused(client, repo):
    offer = _flagged(repo)
    assert client.patch(f"/review/rows/{offer.id}", json={}).status_code == 422


def test_another_tenants_row_is_not_reachable(client, repo):
    offer = _flagged(repo, tenant_id="tenant-2")
    assert client.patch(f"/review/rows/{offer.id}", json={"quantity": 1}).status_code == 404
    assert repo.offers[offer.id].needs_review is True
