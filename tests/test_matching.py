"""Matching: filling a buyer, placing a lot, and refusing to invent things.

The client's stated goal is matching. A filterable list he could build himself; what he
cannot do by hand is spot that three suppliers together cover a hundred-unit order.
"""

import pytest

from app.brain.matching import (
    Requirement,
    Supply,
    fill_requirement,
    group_options,
    place_offer,
)

EAN = "0195949035999"


def want(qty=100, price=620.0, currency="EUR", cp="buyer-1", **kw):
    base = dict(
        id="req-1",
        counterparty_id=cp,
        quantity=qty,
        unit_price=price,
        currency=currency,
        ean=EAN,
        brand="Apple",
        description="Apple iPhone 15 128GB Black",
        description_key="apple iphone 15",
        capacity_gb=128,
        colour="Black",
    )
    return Requirement(**{**base, **kw})


def have(offer_id, qty, price, cp=None, currency="EUR", **kw):
    base = dict(
        id=offer_id,
        counterparty_id=cp or f"seller-{offer_id}",
        quantity=qty,
        unit_price=price,
        currency=currency,
        ean=EAN,
        brand="Apple",
        description="Apple iPhone 15 128GB Black",
        description_key="apple iphone 15",
        capacity_gb=128,
        colour="Black",
    )
    return Supply(**{**base, **kw})


# ---------------------------------------------------------------- single supplier


def test_one_supplier_who_can_cover_it():
    options = fill_requirement(want(qty=100, price=620), [have("a", 120, 579)])

    assert options[0].kind == "exact"
    assert options[0].filled_quantity == 100
    assert options[0].unit_margin == 41.0
    assert options[0].total_margin == 4100.0
    assert options[0].is_complete


def test_margin_percentage_is_on_revenue():
    option = fill_requirement(want(qty=100, price=620), [have("a", 120, 579)])[0]
    assert option.margin_pct == pytest.approx(41.0 / 620 * 100, rel=1e-3)


def test_cheapest_supplier_ranks_first():
    options = fill_requirement(
        want(qty=50), [have("expensive", 60, 600), have("cheap", 60, 560)]
    )
    assert options[0].allocations[0].supply_id == "cheap"


def test_a_supplier_cannot_match_its_own_requirement():
    """The same firm on both sides of the board is not a deal."""
    options = fill_requirement(want(cp="acme"), [have("a", 200, 500, cp="acme")])
    assert options == []


# ---------------------------------------------------------------- combinations


def test_three_suppliers_together_cover_what_none_can_alone():
    """The case the client cannot spot by hand, and the reason this feature exists."""
    options = fill_requirement(
        want(qty=100, price=620),
        [have("a", 40, 570), have("b", 30, 580), have("c", 30, 590)],
    )

    best = options[0]
    assert best.kind == "combination"
    assert best.filled_quantity == 100
    assert best.supplier_count == 3
    assert best.blended_unit_cost == pytest.approx((40 * 570 + 30 * 580 + 30 * 590) / 100)
    assert best.is_complete


def test_combination_is_not_offered_when_one_supplier_suffices():
    """A combination when a single supplier can cover it is needless complexity."""
    options = fill_requirement(
        want(qty=50), [have("big", 100, 580), have("small", 30, 560)]
    )
    assert all(o.kind != "combination" for o in options)


def test_combination_uses_the_cheapest_suppliers_first():
    options = fill_requirement(
        want(qty=100, price=620),
        [have("a", 60, 600), have("b", 60, 550)],
    )
    combo = next(o for o in options if o.kind == "combination")
    assert combo.allocations[0].supply_id == "b"
    assert combo.allocations[0].quantity == 60
    assert combo.allocations[1].quantity == 40


def test_supplier_count_is_capped():
    supply = [have(str(i), 10, 500 + i) for i in range(10)]
    options = fill_requirement(want(qty=100), supply, max_suppliers=3)
    assert all(o.supplier_count <= 3 for o in options)


def test_a_combination_never_promises_more_than_exists():
    options = fill_requirement(want(qty=100), [have("a", 30, 570), have("b", 20, 580)])
    for option in options:
        assert option.filled_quantity <= 50


# ---------------------------------------------------------------- partial fills


def test_shortfall_is_stated_not_hidden():
    options = fill_requirement(want(qty=100), [have("a", 40, 570)])

    assert options[0].kind == "partial"
    assert options[0].filled_quantity == 40
    assert options[0].shortfall == 60
    assert not options[0].is_complete


def test_a_complete_fill_outranks_a_partial_one_of_similar_margin():
    options = fill_requirement(
        want(qty=100, price=620),
        [have("full", 100, 590), have("part", 95, 589)],
    )
    assert options[0].allocations[0].supply_id == "full"


# ---------------------------------------------------------------- near misses

def test_wrong_capacity_is_surfaced_as_a_near_miss():
    """A buyer wanting 256GB when only 512GB exists is a phone call, not a dead end."""
    other = have("a", 100, 620, ean=None, capacity_gb=512)
    options = fill_requirement(want(qty=100, ean=None), [other])

    assert options[0].kind == "near_miss"
    assert "capacity" in options[0].relaxed[0]


def test_wrong_colour_is_surfaced():
    other = have("a", 100, 570, ean=None, colour="Blue")
    options = fill_requirement(want(qty=100, ean=None), [other])
    assert options[0].kind == "near_miss"
    assert "colour" in options[0].relaxed[0]


def test_a_near_miss_never_outranks_a_real_fill():
    options = fill_requirement(
        want(qty=100, price=620, ean=None),
        [have("exact", 100, 580, ean=None), have("other", 100, 560, ean=None, colour="Blue")],
    )
    assert options[0].kind == "exact"


def test_a_buyer_without_an_ean_still_matches_a_seller_with_one():
    """The single most consequential case in the real data. Sellers publish EANs;
    buyers write want-to-buy lists by hand with no barcode. Comparing an EAN-derived
    identity key against a spec-derived one matched 3 of 225 real requirements."""
    buyer = want(qty=50, price=620, ean=None)
    seller = have("a", 100, 579)          # has an EAN

    options = fill_requirement(buyer, [seller])

    assert options
    assert options[0].kind == "exact"
    assert options[0].total_margin == 2050.0


def test_spec_match_still_respects_capacity_and_colour():
    """The fallback must not become a loose match — it is what stops a 512GB Blue
    being offered against a request for 128GB Black."""
    buyer = want(qty=50, ean=None, capacity_gb=128, colour="Black")

    assert fill_requirement(
        buyer, [have("a", 100, 579, capacity_gb=512)], include_near_misses=False
    ) == []
    assert fill_requirement(
        buyer, [have("b", 100, 579, colour="Blue")], include_near_misses=False
    ) == []


def test_a_different_product_is_not_a_near_miss():
    """Two different EANs alone prove nothing — a case and a phone differ too."""
    unrelated = Supply(
        id="x", counterparty_id="s", quantity=100, unit_price=2.5,
        ean="4252011907762", description_key="4smarts car charger", description="Charger",
    )
    assert fill_requirement(want(ean=None), [unrelated]) == []


def test_near_misses_can_be_excluded():
    other = have("a", 100, 570, ean=None, colour="Blue")
    assert fill_requirement(want(ean=None), [other], include_near_misses=False) == []


# ---------------------------------------------------------------- currency


def test_margin_is_not_computed_across_currencies():
    """Inventing a conversion rate produces a plausible number that is wrong by however
    far the rate has moved — and nobody checks a plausible number."""
    options = fill_requirement(
        want(price=620, currency="EUR"), [have("a", 120, 579, currency="USD")]
    )

    assert options[0].total_margin is None
    assert any("margin not computed" in w for w in options[0].warnings)


def test_a_currency_mismatch_is_still_returned_as_a_lead():
    options = fill_requirement(want(currency="EUR"), [have("a", 120, 579, currency="USD")])
    assert len(options) == 1


def test_unknown_currency_is_not_treated_as_a_mismatch():
    """Unlabelled prices within one list are almost always the same currency."""
    options = fill_requirement(want(price=620, currency=None), [have("a", 120, 579, currency=None)])
    assert options[0].total_margin == 4100.0


# ---------------------------------------------------------------- ranking


def test_extra_suppliers_are_penalised():
    """Two options, same margin: the simpler one wins."""
    single = fill_requirement(want(qty=60, price=620), [have("solo", 60, 580)])[0]
    combo = fill_requirement(
        want(qty=60, price=620), [have("a", 30, 580), have("b", 30, 580)]
    )[0]

    assert single.total_margin == combo.total_margin
    assert single.score > combo.score


def test_cross_border_combinations_are_penalised_and_warned_about():
    options = fill_requirement(
        want(qty=100, price=620),
        [have("a", 50, 570, country="DE"), have("b", 50, 570, country="PL")],
    )
    combo = next(o for o in options if o.kind == "combination")
    assert any("crosses" in w for w in combo.warnings)


def test_options_with_no_margin_sort_last():
    options = fill_requirement(
        want(price=620, currency="EUR"),
        [have("priced", 200, 580, currency="EUR"), have("unknown", 200, 500, currency="USD")],
    )
    assert options[0].allocations[0].supply_id == "priced"


# ---------------------------------------------------------------- placing a lot


def test_a_lot_is_split_across_several_buyers():
    """Splitting usually beats selling whole: the one buyer who can absorb everything
    knows it, and prices accordingly."""
    offer = have("lot", 200, 560)
    buyers = [
        Requirement(id="b1", counterparty_id="buyer-1", quantity=80, unit_price=620,
                    currency="EUR", ean=EAN, description_key="apple iphone 15",
                    capacity_gb=128, colour="Black"),
        Requirement(id="b2", counterparty_id="buyer-2", quantity=70, unit_price=610,
                    currency="EUR", ean=EAN, description_key="apple iphone 15",
                    capacity_gb=128, colour="Black"),
        Requirement(id="b3", counterparty_id="buyer-3", quantity=50, unit_price=600,
                    currency="EUR", ean=EAN, description_key="apple iphone 15",
                    capacity_gb=128, colour="Black"),
    ]

    options = place_offer(offer, buyers)
    split = next(o for o in options if len(o.allocations) > 1)

    assert split.filled_quantity == 200
    assert split.total_margin == pytest.approx(80 * 60 + 70 * 50 + 50 * 40)


def test_placement_takes_the_best_paying_buyers_first():
    offer = have("lot", 100, 560)
    buyers = [
        Requirement(id="low", counterparty_id="b-low", quantity=100, unit_price=580,
                    currency="EUR", ean=EAN),
        Requirement(id="high", counterparty_id="b-high", quantity=100, unit_price=640,
                    currency="EUR", ean=EAN),
    ]
    assert place_offer(offer, buyers)[0].allocations[0].counterparty_id == "b-high"


# ---------------------------------------------------------------- presentation


def test_options_are_grouped_into_labelled_sections():
    """The client asked for labelled sections, not one flat ranked list."""
    options = fill_requirement(
        want(qty=100, price=620, ean=None),
        [
            have("solo", 100, 580, ean=None),
            have("a", 60, 570, ean=None),
            have("b", 50, 575, ean=None),
            have("small", 20, 560, ean=None),
            have("other", 100, 560, ean=None, colour="Blue"),
        ],
    )
    groups = group_options(options)

    assert "single supplier" in groups
    assert "different spec" in groups
    assert all(o.kind == "near_miss" for o in groups["different spec"])


# ---------------------------------------------------------------- robustness


def test_no_supply_yields_no_options():
    assert fill_requirement(want(), []) == []


def test_offers_without_a_price_are_ignored():
    assert fill_requirement(want(), [have("a", 100, None)]) == []


def test_offers_with_no_quantity_are_ignored():
    assert fill_requirement(want(), [have("a", 0, 570)]) == []


def test_buyer_without_a_price_still_gets_options():
    """He may not have stated a price; the lead is still worth showing."""
    options = fill_requirement(want(price=None), [have("a", 200, 570)])
    assert len(options) == 1
    assert options[0].total_margin is None
    assert any("no price" in w for w in options[0].warnings)
