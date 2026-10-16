"""The held-list queue over HTTP, which is the path the dashboard takes.

The unit tests cover what each answer means. These cover the part only the API can get
wrong: that the diff is computed against the board as it stands now, that a list cannot
be settled twice, and that 'partial' with nothing ticked is refused before it reaches
the board rather than quietly doing nothing.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_caller, current_tenant
from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.dependencies import set_repository
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
    ["Samsung S25 512GB Mint", "18", "1045"],
    ["Google Pixel 10 128GB Obsidian", "30", "549"],
]
AFTERNOON = [
    HEADERS,
    ["Apple iPhone 16 128GB Black", "50", "655"],
    ["Samsung S25 512GB Mint", "18", "1045"],
    ["Motorola Edge 70 Pro 5G 8/256GB Titan", "80", "319"],
]


@pytest.fixture
def repo():
    store = InMemoryRepository([TENANT])
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


def _email(rows, *, message_id, at):
    return InboundEmail(
        tenant_id="tenant-1", message_id=message_id,
        from_email="sales@supplier.example", subject="WTS stock list",
        received_at=at, body_text="Our stock list.",
        attachments=[Attachment(filename="stock.xlsx", kind="table", rows=rows)],
    )


@pytest.fixture
def held(repo):
    process_email(_email(MORNING, message_id="<m1@x>", at=datetime(2026, 10, 16, 9, 0)), repo)
    process_email(_email(AFTERNOON, message_id="<m2@x>", at=datetime(2026, 10, 16, 16, 0)), repo)
    return repo.imports[-1].id


def test_the_queue_lists_what_is_waiting(client, held):
    body = client.get("/review/held").json()

    assert body["waiting"] == 1
    item = body["items"][0]
    assert item["id"] == held
    assert item["side"] == "sell"
    assert item["difference_summary"]
    assert item["reason"]


def test_the_detail_view_is_the_diff(client, held):
    body = client.get(f"/review/held/{held}").json()

    assert body["counts"]["appeared"] == 1     # the Motorola
    assert body["counts"]["vanished"] == 1     # the Pixel
    assert body["counts"]["price"] == 1        # the repriced iPhone
    assert body["counts"]["unchanged"] == 1    # the Samsung

    priced = [c for c in body["changes"] if c["kind"] == "price"][0]
    assert priced["previous_price"] == 679.0
    assert priced["unit_price"] == 655.0
    assert priced["price_move"] < 0


def test_an_unknown_list_is_a_404(client):
    assert client.get("/review/held/does-not-exist").status_code == 404


def test_same_leaves_the_board_alone(client, repo, held):
    before = {o.identity_key: (o.quantity, o.unit_price) for o in repo.live_offers()}

    body = client.post(f"/review/held/{held}/resolve", json={"resolution": "same"}).json()

    assert body["closed"] == 0
    assert body["applied"] == 0
    assert body["refreshed"] == 2
    after = {o.identity_key: (o.quantity, o.unit_price) for o in repo.live_offers()}
    assert after == before


def test_new_applies_the_list_including_closures(client, repo, held):
    body = client.post(f"/review/held/{held}/resolve", json={"resolution": "new"}).json()

    assert body["resolution"] == "new"
    assert body["applied"] >= 1
    assert body["closed"] == 1            # the Pixel
    live = {o.identity_key for o in repo.live_offers()}
    assert any("otorola" not in k for k in live)


def test_partial_applies_only_the_ticked_rows_and_closes_nothing(client, repo, held):
    detail = client.get(f"/review/held/{held}").json()
    motorola = [c for c in detail["changes"] if c["kind"] == "appeared"][0]

    body = client.post(
        f"/review/held/{held}/resolve",
        json={"resolution": "partial", "identity_keys": [motorola["identity_key"]]},
    ).json()

    assert body["closed"] == 0
    assert body["applied"] == 1
    assert motorola["identity_key"] in {o.identity_key for o in repo.live_offers()}


def test_partial_with_nothing_ticked_is_refused(client, held):
    response = client.post(
        f"/review/held/{held}/resolve",
        json={"resolution": "partial", "identity_keys": []},
    )
    assert response.status_code == 422


def test_an_unknown_resolution_is_refused(client, held):
    response = client.post(
        f"/review/held/{held}/resolve", json={"resolution": "whatever"}
    )
    assert response.status_code == 422


def test_a_list_cannot_be_settled_twice(client, held):
    """Two people on the queue, or one person clicking again on a slow connection."""
    first = client.post(f"/review/held/{held}/resolve", json={"resolution": "same"})
    second = client.post(f"/review/held/{held}/resolve", json={"resolution": "new"})

    assert first.status_code == 200
    assert second.status_code == 409


def test_a_settled_list_leaves_the_queue(client, held):
    client.post(f"/review/held/{held}/resolve", json={"resolution": "same"})

    assert client.get("/review/held").json()["waiting"] == 0
