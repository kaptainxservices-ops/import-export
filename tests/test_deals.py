"""What a deal may commit to, and what a deal is worth.

The specification names the over-allocation rule twice, which is unusual enough to take
at its word: the system must block assigning 121 of a 120 lot, and *warn* if the same lot
is committed to a second deal. Those two are deliberately different strengths and most of
this file is about keeping them that way round.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.brain.deals import Commitment, check_allocation, stale_after_days
from app.db.models import Allocation, Deal

# ---------------------------------------------------------------- over-allocation


def test_a_lot_cannot_promise_more_than_it_holds():
    """Straight from the spec: 121 of a 120 lot. Not a judgement call — one of those
    units does not exist, and the trader finds out when a shipment arrives short."""
    verdict = check_allocation(wanted=121, lot_quantity=120, existing=[])

    assert verdict.allowed is False
    assert "120" in verdict.reason


def test_the_whole_lot_is_allowed():
    assert check_allocation(wanted=120, lot_quantity=120, existing=[]).allowed is True


def test_what_other_open_deals_have_taken_is_deducted():
    """40 of a 120 lot are spoken for, so 90 cannot be promised."""
    taken = [Commitment(deal_id="d1", deal_reference=114, quantity=40)]

    verdict = check_allocation(wanted=90, lot_quantity=120, existing=taken)

    assert verdict.allowed is False
    assert verdict.remaining == 80
    assert "already promised" in verdict.reason


def test_a_closed_deal_releases_what_it_held():
    """A deal that is over no longer holds stock. Counting it would shrink the order
    book by every deal ever done."""
    finished = [Commitment(deal_id="d1", deal_reference=114, quantity=100, is_open=False)]

    assert check_allocation(wanted=120, lot_quantity=120, existing=finished).allowed is True


def test_raising_a_deals_own_allocation_is_not_measured_against_itself():
    """Editing a leg from 40 to 50 compares against the *other* deals. Without this,
    increasing any allocation is impossible — it always collides with its own claim."""
    mine = [Commitment(deal_id="d1", deal_reference=114, quantity=40)]

    verdict = check_allocation(wanted=50, lot_quantity=120, existing=mine, deal_id="d1")

    assert verdict.allowed is True


# ---------------------------------------------------------------- double commitment


def test_committing_the_same_lot_twice_is_warned_about_not_blocked():
    """A broker chasing two buyers for one lot expects to lose one of them. A system
    that refused would be a system he works around — what he cannot afford is doing it
    without noticing."""
    elsewhere = [Commitment(deal_id="d1", deal_reference=114, quantity=40)]

    verdict = check_allocation(wanted=30, lot_quantity=120, existing=elsewhere, deal_id="d2")

    assert verdict.allowed is True
    assert verdict.warnings
    assert "#114" in verdict.warnings[0]
    assert "70 of 120" in verdict.warnings[0]


def test_a_lot_with_no_stated_quantity_is_allowed_with_a_warning():
    """Plenty of real offers arrive with no quantity. Refusing to let the trader record
    a deal because a supplier was vague is the system getting in the way of the trade."""
    verdict = check_allocation(wanted=50, lot_quantity=None, existing=[])

    assert verdict.allowed is True
    assert verdict.remaining is None
    assert "no stated quantity" in verdict.warnings[0]


@pytest.mark.parametrize("wanted", [0, -5])
def test_a_quantity_must_be_positive(wanted):
    assert check_allocation(wanted=wanted, lot_quantity=120, existing=[]).allowed is False


# ---------------------------------------------------------------- margin


def _leg(side, qty, price, cp="cp"):
    return Allocation(
        id=f"a-{side}-{qty}", deal_id="d1", side=side, counterparty_id=cp,
        quantity=qty, unit_price=price, currency="USD",
    )


def test_margin_across_one_seller_and_three_buyers():
    """The spec's own worked example: 120 bought at 905, sold 50/40/30 at 955/948/935."""
    deal = Deal(
        id="d1", tenant_id="t", reference=114,
        allocations=[
            _leg("buy", 120, 905.0),
            _leg("sell", 50, 955.0),
            _leg("sell", 40, 948.0),
            _leg("sell", 30, 935.0),
        ],
    )

    assert deal.margin == pytest.approx(5120.0)
    assert len(deal.buying) == 1
    assert len(deal.selling) == 3


def test_a_one_sided_deal_has_no_margin():
    """Negotiating with a seller before a buyer exists is explicitly supported. It has a
    cost, not a margin — and printing one would be the same error as inventing a rate."""
    deal = Deal(id="d1", tenant_id="t", reference=115, allocations=[_leg("buy", 120, 905.0)])

    assert deal.margin is None
    assert deal.is_open


def test_a_missing_price_leaves_the_margin_unknown():
    deal = Deal(
        id="d1", tenant_id="t", reference=116,
        allocations=[_leg("buy", 100, None), _leg("sell", 100, 955.0)],
    )

    assert deal.margin is None


def test_an_allocations_value_is_frozen_at_agreement():
    """The numbers live on the allocation, not read through to the offer. A supplier's
    list expires every morning; a deal struck yesterday at 905 did not."""
    assert _leg("buy", 120, 905.0).value == pytest.approx(108600.0)


# ---------------------------------------------------------------- the nudge


def test_a_deal_nobody_has_touched_surfaces_after_the_threshold():
    """'No update in 8 days'. A staleness nudge, not a stage model — the client rejected
    pipeline stages on the grounds that he would not maintain them."""
    now = datetime(2026, 8, 20)

    assert stale_after_days(now - timedelta(days=9), now) == 9
    assert stale_after_days(now - timedelta(days=8), now) == 8
    assert stale_after_days(now - timedelta(days=2), now) is None
    assert stale_after_days(None, now) is None


# ---------------------------------------------------------------- the endpoints


def test_every_deal_endpoint_requires_a_token(client):
    """Deals hold what a client agreed and at what price. If anything on this board
    needs a session, it is this."""
    from app.db.memory import InMemoryRepository
    from app.db.models import TenantConfig
    from app.dependencies import set_repository

    set_repository(InMemoryRepository([TenantConfig(id="t1")]))
    try:
        assert client.get("/deals").status_code == 401
        assert client.post("/deals", json={"title": "x"}).status_code == 401
        assert client.patch("/deals/x", json={"status": "y"}).status_code == 401
        assert client.post("/deals/x/close", json={"outcome": "won"}).status_code == 401
        assert client.post("/deals/x/allocations", json={}).status_code == 401
        assert client.delete("/deals/x/allocations/y").status_code == 401
    finally:
        set_repository(None)


def test_the_tenant_cannot_be_passed_in():
    """A tenant id in the request is a request to work on someone else's deals."""
    import inspect

    from app.api import deals as module

    endpoints = [
        module.list_deals, module.create_deal, module.update_deal,
        module.close_deal, module.add_allocation, module.remove_allocation,
    ]
    for endpoint in endpoints:
        assert "tenant_id" not in inspect.signature(endpoint).parameters

    for shape in (module.NewDeal, module.DealPatch, module.NewAllocation, module.CloseDeal):
        assert "tenant_id" not in shape.model_fields


def test_an_outcome_must_be_won_or_lost():
    from pydantic import ValidationError

    from app.api.deals import CloseDeal

    assert CloseDeal(outcome="won").outcome == "won"
    with pytest.raises(ValidationError):
        CloseDeal(outcome="maybe")


def test_a_lost_deal_does_not_require_a_reason():
    """A trader closing out at speed should not be blocked by a text box, and a lost
    deal with no reason recorded is still better than no record."""
    from app.api.deals import CloseDeal

    assert CloseDeal(outcome="lost").loss_reason is None


def test_touching_the_status_resets_the_nudge_clock_and_a_title_does_not():
    """The eight-day prompt asks whether anyone has *said* anything. Renaming a deal is
    not news, and letting it reset the clock would make the nudge meaningless."""
    from app.api.deals import DealPatch

    assert "status_updated_at" in DealPatch(status="deposit received").changes()
    assert "status_updated_at" not in DealPatch(title="Meridian 100").changes()


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_an_allocation_takes_either_side(side):
    from app.api.deals import NewAllocation

    assert NewAllocation(side=side, counterparty_id="c", quantity=10).side == side


def test_an_allocation_is_refused_without_a_real_side_or_quantity():
    from pydantic import ValidationError

    from app.api.deals import NewAllocation

    with pytest.raises(ValidationError):
        NewAllocation(side="maybe", counterparty_id="c", quantity=10)
    with pytest.raises(ValidationError):
        NewAllocation(side="buy", counterparty_id="c", quantity=0)
