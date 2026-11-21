"""The match list's variant specs, match types, buyer view and the polish guard."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_tenant
from app.brain.llm import polish as polish_module
from app.brain.llm.polish import same_figures
from app.db.models import BoardOffer, CounterpartyConfig


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    from app.brain import window

    now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    real = window.window_start
    monkeypatch.setattr(
        window, "window_start", lambda now_=None, days=None: real(now_ or now, days)
    )


def row(
    row_id: str, side: str, who: str, description: str, quantity: int, price: float | None,
    *, capacity: int | None = None, ram: int | None = None, colour: str | None = None,
    brand: str = "Samsung",
) -> BoardOffer:
    return BoardOffer(
        id=row_id, counterparty_id=who, counterparty_name=who.title(), side=side,
        description=description, quantity=quantity, unit_price=price, currency="USD",
        identity_key=row_id, match_key=None, description_key=None, ean=None,
        brand=brand, capacity_gb=capacity, colour=colour, category="phone", ram_gb=ram,
        first_seen_at="2026-10-29T08:00:00+00:00", last_confirmed_at="2026-10-30T09:00:00+00:00",
    )


BOARD = [
    row("want-a17", "buy", "acme", "Samsung Galaxy A17 5G 128GB", 100, None, capacity=128),
    row("want-s24", "buy", "acme", "Samsung Galaxy S24 256GB", 20, None, capacity=256),
    row("other", "buy", "zeta", "Samsung Galaxy A17 5G 128GB", 5, None, capacity=128),
    row("a17-6", "sell", "gulf", "Samsung Galaxy A17 5G 6/128GB", 60, 150.0, capacity=128, ram=6),
    row("a17-4", "sell", "euro", "Samsung Galaxy A17 5G 4/128GB", 100, 140.0, capacity=128, ram=4),
    row("s24", "sell", "gulf", "Samsung Galaxy S24 256GB", 20, 600.0, capacity=256),
]


class _Repo:
    def __init__(self, rows):
        self.rows = rows
        self.parties = {
            "acme": CounterpartyConfig(id="acme", primary_email="buy@acme.example", name="Acme"),
        }

    def load_board(self, tenant_id, side, limit=5000):
        return [r for r in self.rows if r.side == side]

    def get_board_offer(self, tenant_id, offer_id):
        return next((r for r in self.rows if r.id == offer_id), None)

    def get_counterparty(self, tenant_id, counterparty_id):
        return self.parties.get(counterparty_id)


@pytest.fixture
def client():
    from app.main import app

    repo = _Repo(BOARD)
    app.dependency_overrides[current_tenant] = lambda: ("t1", repo)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _options(body):
    return [o for options in body["groups"].values() for o in options]


def test_every_leg_carries_its_spec_for_the_variant_filter(client):
    body = client.get("/matches/fill?offer_id=want-a17").json()
    legs = {a["offer_id"]: a for o in _options(body) for a in o["allocations"]}
    assert legs["a17-6"]["ram_gb"] == 6
    assert legs["a17-4"]["ram_gb"] == 4
    assert legs["a17-6"]["capacity_gb"] == 128


def test_the_subject_names_its_buyer(client):
    body = client.get("/matches/fill?offer_id=want-a17").json()
    assert body["subject_counterparty_id"] == "acme"
    assert body["subject_counterparty_name"] == "Acme"


def test_options_are_typed_and_ranked(client):
    options = _options(client.get("/matches/fill?offer_id=want-a17").json())
    assert sorted(o["rank"] for o in options) == list(range(len(options)))
    for o in options:
        if o["kind"] == "near_miss":
            assert o["match_type"] == "near_miss"
        elif o["shortfall"]:
            assert o["match_type"] == "partial"
        else:
            assert o["match_type"] == "full"
    # 60 of 100 from one supplier is partial; the 100-unit lot alone is full.
    by_lots = {tuple(a["offer_id"] for a in o["allocations"]): o for o in options}
    assert by_lots[("a17-6",)]["match_type"] == "partial"
    assert by_lots[("a17-4",)]["match_type"] == "full"


def test_the_buyer_view_lists_every_requirement_with_its_matches(client):
    body = client.get("/matches/buyer?buyer_id=acme").json()
    assert body["name"] == "Acme"
    assert body["email"] == "buy@acme.example"
    ids = [r["subject_id"] for r in body["requirements"]]
    assert sorted(ids) == ["want-a17", "want-s24"]
    s24 = next(r for r in body["requirements"] if r["subject_id"] == "want-s24")
    assert any(a["offer_id"] == "s24" for o in _options(s24) for a in o["allocations"])


def test_an_unknown_buyer_is_not_found(client):
    assert client.get("/matches/buyer?buyer_id=nobody").status_code == 404


# ---------------------------------------------------------------- polish


def test_the_figure_guard_accepts_reworded_text():
    before = "Hi,\n100 pcs A17 6/128 at $150.00 each, total $15,000.00"
    after = "Hello,\nWe can offer 100 pcs of the A17 6/128 at $150.00 each (total $15,000.00)."
    assert same_figures(before, after)


@pytest.mark.parametrize(
    "after",
    [
        "100 pcs A17 6/128 at $150 each, total $15,000.00",      # reformatted price
        "100 pcs A17 6/128 at $149.00 each, total $15,000.00",   # changed price
        "A17 6/128 at $150.00 each, total $15,000.00",           # dropped quantity
        "100 pcs A17 6/128 at $150.00 each, total $15,000.00, delivery in 3 days",  # new
    ],
)
def test_the_figure_guard_refuses_any_changed_number(after):
    assert not same_figures("100 pcs A17 6/128 at $150.00 each, total $15,000.00", after)


def test_a_rewrite_that_changes_a_number_is_thrown_away(client, monkeypatch):
    from app.brain.llm.client import Answer

    monkeypatch.setattr(polish_module, "available", lambda: True)
    monkeypatch.setattr(
        polish_module, "ask", lambda *a, **k: Answer(text="10 pcs at $1.00", model="m")
    )
    response = client.post("/matches/polish", json={"text": "100 pcs at $150.00"})
    assert response.status_code == 422


def test_a_clean_rewrite_is_returned(client, monkeypatch):
    from app.brain.llm.client import Answer

    monkeypatch.setattr(polish_module, "available", lambda: True)
    monkeypatch.setattr(
        polish_module, "ask",
        lambda *a, **k: Answer(text="We can offer 100 pcs at $150.00.", model="m"),
    )
    response = client.post("/matches/polish", json={"text": "100 pcs at $150.00"})
    assert response.status_code == 200
    assert response.json()["text"] == "We can offer 100 pcs at $150.00."


def test_the_seller_view_lists_every_lot_with_its_buyers(client):
    body = client.get("/matches/seller?buyer_id=x&seller_id=gulf").json()
    ids = sorted(r["subject_id"] for r in body["lots"])
    assert ids == ["a17-6", "s24"]
    a17 = next(r for r in body["lots"] if r["subject_id"] == "a17-6")
    buyers = {a["counterparty_id"] for o in _options(a17) for a in o["allocations"]}
    assert "acme" in buyers
    assert body["lots"][0]["direction"] == "place"


def test_an_unknown_seller_is_not_found(client):
    assert client.get("/matches/seller?seller_id=nobody").status_code == 404
