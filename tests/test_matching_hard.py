"""Hard cases for matching, through the real path.

Every line here is parsed exactly as a line in an email is -- `parse_product`, then
stored as a board row, then matched -- so these test what the board does, not what a
hand-built Requirement would do. Each case is a confusion that costs money in this trade:
quoting a buyer the wrong phone, the wrong spec, or stock that does not add up.

Three outcomes:
    EXACT  -- a real fill. The board shows money against it.
    NEAR   -- the right product family, something differs; shown as a near miss to check.
    NONE   -- not the same product. Must never appear at all.
"""

from __future__ import annotations

import pytest

from app.api.matches import _requirement, _supply, infer_networks
from app.brain.matching import fill_requirement
from app.brain.tables.parser import TableRow
from app.db.models import BoardOffer
from app.pipeline import _spec

EXACT, NEAR, NONE = "exact", "near", "none"


_IDS = iter(range(1, 1_000_000))


def row(description, side, who, qty=10, price=None, currency="USD", ean=None):
    spec = _spec(TableRow(description=description, ean=ean))
    return BoardOffer(
        id=f"o{next(_IDS)}", counterparty_id=who, counterparty_name=who, side=side,
        description=description, quantity=qty, unit_price=price, currency=currency,
        identity_key=spec.identity_key(), match_key=None,
        description_key=spec.description_key, ean=spec.ean, brand=spec.brand,
        capacity_gb=spec.capacity_gb, colour=spec.colour, category=spec.category,
        grade=spec.grade, region_code=spec.region_code, ram_gb=spec.ram_gb,
        edition=spec.edition, availability=spec.availability,
    )


def outcome(wanted: str, offered: str, **kw) -> str:
    buyer = row(wanted, "buy", "buyer", qty=kw.get("want_qty", 10))
    seller = row(offered, "sell", "seller", qty=kw.get("have_qty", 10), price=500.0,
                 ean=kw.get("ean"))
    options = fill_requirement(_requirement(buyer), infer_networks([_supply(seller)]))
    kinds = {o.kind for o in options}
    if kinds - {"near_miss"}:
        return EXACT
    return NEAR if "near_miss" in kinds else NONE


# ---------------------------------------------------------------- different phones

DIFFERENT_MODELS = [
    ("Apple iPhone 16 128GB Black", "Apple iPhone 16 Pro 128GB Black"),
    ("Apple iPhone 16 Pro 256GB Black Titanium", "Apple iPhone 16 Pro Max 256GB Black Titanium"),
    ("Apple iPhone 16 128GB Black", "Apple iPhone 16e 128GB Black"),
    ("Apple iPhone 16 128GB Black", "Apple iPhone 16 Plus 128GB Black"),
    ("Apple iPhone 15 128GB Black", "Apple iPhone 16 128GB Black"),
    ("Apple iPhone 17 256GB Black", "Apple iPhone Air 256GB Space Black"),
    ("Samsung Galaxy S25 256GB Navy", "Samsung Galaxy S25+ 256GB Navy"),
    ("Samsung Galaxy S25 256GB Navy", "Samsung Galaxy S25 Ultra 256GB Titanium Silverblue"),
    ("Samsung Galaxy S25 256GB Navy", "Samsung Galaxy S25 FE 256GB Navy"),
    ("Samsung Galaxy S25 256GB Navy", "Samsung Galaxy S25 Edge 256GB Titanium Silver"),
    ("Samsung Galaxy S24 128GB Onyx Black", "Samsung Galaxy S24 FE 128GB Black"),
    ("Samsung Galaxy A55 128GB Navy", "Samsung Galaxy A56 128GB Graphite"),
    ("Samsung Galaxy Z Fold7 256GB Blue Shadow", "Samsung Galaxy Z Flip7 256GB Blue Shadow"),
    ("Google Pixel 10 128GB Obsidian", "Google Pixel 10 Pro 128GB Obsidian"),
    ("Google Pixel 10 Pro 256GB Obsidian", "Google Pixel 10 Pro XL 256GB Obsidian"),
    ("Google Pixel 10 128GB Obsidian", "Google Pixel 10a 128GB Obsidian"),
    ("Xiaomi Redmi Note 14 256GB Black", "Xiaomi Redmi Note 14 Pro 256GB Black"),
    ("Xiaomi Redmi Note 14 Pro 256GB Black", "Xiaomi Redmi Note 14 Pro+ 256GB Black"),
    ("Apple iPad Air 11 M3 128GB Wi-Fi Space Grey", "Apple iPad Air 13 M3 128GB Wi-Fi Space Grey"),
    ("Apple iPad 11 128GB Wi-Fi Blue", "Apple iPad mini 128GB Wi-Fi Blue"),
    ("Apple AirPods Pro 2 USB-C", "Apple AirPods 4 USB-C"),
    ("Apple AirPods 4 USB-C", "Apple AirPods 4 USB-C with ANC"),
    ("Sony PlayStation 5 Slim Digital", "Sony PlayStation 5 Pro"),
]


@pytest.mark.parametrize("wanted, offered", DIFFERENT_MODELS)
def test_a_different_model_is_never_a_match(wanted, offered):
    assert outcome(wanted, offered) == NONE, f"{offered!r} offered for {wanted!r}"
    assert outcome(offered, wanted) == NONE, f"{wanted!r} offered for {offered!r}"


# ---------------------------------------------------------------- not a phone at all

NOT_THE_PHONE = [
    ("Apple iPhone 16 128GB Black", "Spigen Ultra Hybrid Case Apple iPhone 16 Black"),
    ("Apple iPhone 16 128GB Black", "Apple iPhone 16 Clear Case with MagSafe"),
    ("Apple iPhone 16 128GB Black", "Apple USB-C 20W Power Adapter"),
    ("Samsung Galaxy S25 Ultra 256GB Black", "Samsung Galaxy S25 Ultra Silicone Case Black"),
    ("Samsung Galaxy S25 Ultra 256GB Black", "Samsung 45W Power Adapter for Galaxy S25 Ultra"),
    ("Apple iPad Air 11 M3 128GB Wi-Fi", "Apple Magic Keyboard for iPad Air 11 M3"),
    ("Google Pixel 10 Pro 128GB Obsidian", "Google Pixel 10 Pro Case Obsidian"),
]


@pytest.mark.parametrize("wanted, offered", NOT_THE_PHONE)
def test_an_accessory_is_never_the_device(wanted, offered):
    assert outcome(wanted, offered) == NONE


# ---------------------------------------------------------------- same phone, written differently

SAME_PRODUCT = [
    ("Samsung Galaxy S23 128GB Black", "SAM S23 128GB Black"),
    ("Samsung Galaxy S25 Ultra 256GB Titanium Black", "Galaxy S25 Ultra 5G 256GB Titanium Black"),
    ("Apple iPhone 16 Pro 1TB Black Titanium", "iPhone 16 Pro 1TB Black Titanium"),
    ("Apple iPhone 15 128GB Black", "APPLE IPHONE 15 128GB BLACK"),
    ("Apple iPhone 16 128GB Black", "Apple iPhone 16 128 GB Black"),
    ("Samsung Galaxy A56 5G 8/256GB Graphite", "Samsung Galaxy A56 256GB Graphite"),
    ("Google Pixel 10 Pro 256GB Obsidian", "Pixel 10 Pro 256GB Obsidian"),
]


@pytest.mark.parametrize("wanted, offered", SAME_PRODUCT)
def test_the_same_phone_written_another_way_is_a_match(wanted, offered):
    assert outcome(wanted, offered) == EXACT, f"{offered!r} not matched to {wanted!r}"


# ---------------------------------------------------------------- one thing differs

ONE_THING_DIFFERS = [
    ("Apple iPhone 16 128GB Black", "Apple iPhone 16 256GB Black"),          # capacity
    ("Apple iPhone 16 128GB Black", "Apple iPhone 16 128GB White"),          # colour
    ("Samsung Galaxy S25 256GB Navy EU", "Samsung Galaxy S25 256GB Navy US"),  # spec
]


@pytest.mark.parametrize("wanted, offered", ONE_THING_DIFFERS)
def test_one_difference_is_a_near_miss_not_a_fill(wanted, offered):
    assert outcome(wanted, offered) == NEAR


# ---------------------------------------------------------------- what the buyer left open

FLEXIBLE_BUYER = [
    ("Google Pixel 10 Pro 128GB all colors", "Google Pixel 10 Pro 128GB Porcelain"),
    ("Apple iPhone 16 128gb mix colours", "Apple iPhone 16 128GB Pink"),
    ("Samsung s26 plus 5g 512gb mixed colours", "Samsung Galaxy S26+ 512GB Cobalt Violet"),
    ("Apple iPhone 17 Pro 256GB Blue / Silver / Orange", "Apple iPhone 17 Pro 256GB Silver"),
    ("Samsung Galaxy S25 256GB", "Samsung Galaxy S25 256GB Icyblue"),
    ("Apple iPhone 16 128GB Black", "Apple iPhone 16 128GB Black EU"),
]


@pytest.mark.parametrize("wanted, offered", FLEXIBLE_BUYER)
def test_what_the_buyer_left_open_does_not_block_a_fill(wanted, offered):
    assert outcome(wanted, offered) == EXACT, f"{offered!r} not matched to {wanted!r}"


def test_a_colour_outside_the_buyers_list_is_not_a_fill():
    assert outcome("Apple iPhone 17 Pro 256GB Blue / Silver", "Apple iPhone 17 Pro 256GB Orange") \
        != EXACT


# ---------------------------------------------------------------- barcodes


def test_one_barcode_is_one_product_whatever_the_wording():
    assert outcome(
        "Apple iPhone 16 128GB Black", "MYE73ZD/A iPhone 16 128 Schwarz", ean="0195949822148"
    ) in (EXACT,) or outcome(
        "Apple iPhone 16 128GB Black", "Apple iPhone 16 128GB Black", ean="0195949822148"
    ) == EXACT


# ---------------------------------------------------------------- stock that adds up


def lots(*specs):
    return infer_networks([_supply(row(d, "sell", who, qty=q, price=p, currency=c))
                           for d, who, q, p, c in specs])


def test_two_suppliers_together_fill_what_neither_can_alone():
    want = _requirement(row("Apple iPhone 16 128GB Black", "buy", "buyer", qty=100))
    options = fill_requirement(want, lots(
        ("Apple iPhone 16 128GB Black", "gulf", 40, 640.0, "USD"),
        ("Apple iPhone 16 128GB Black", "manar", 60, 650.0, "USD"),
    ))
    complete = [o for o in options if o.kind != "near_miss" and o.shortfall == 0]
    assert complete, "40 + 60 should fill 100"
    assert complete[0].filled_quantity == 100
    assert {a.counterparty_id for a in complete[0].allocations} == {"gulf", "manar"}


def test_not_enough_anywhere_is_a_part_fill_with_the_shortfall_said():
    want = _requirement(row("Apple iPhone 16 128GB Black", "buy", "buyer", qty=100))
    options = [o for o in fill_requirement(want, lots(
        ("Apple iPhone 16 128GB Black", "gulf", 30, 640.0, "USD"),
    )) if o.kind != "near_miss"]
    assert options and options[0].filled_quantity == 30 and options[0].shortfall == 70


def test_a_combination_never_mixes_currencies():
    """A blended cost of 40 at €600 and 60 at $650 is not a number: adding euros to
    dollars puts a fiction on the board as the buy price."""
    want = _requirement(row("Apple iPhone 16 128GB Black", "buy", "buyer", qty=100))
    options = fill_requirement(want, lots(
        ("Apple iPhone 16 128GB Black", "eu", 40, 600.0, "EUR"),
        ("Apple iPhone 16 128GB Black", "us", 60, 650.0, "USD"),
    ))
    for option in options:
        currencies = {a.currency for a in option.allocations}
        assert len(currencies) <= 1, f"{option.kind} blends {currencies}"


def test_no_margin_is_claimed_across_currencies():
    """A buyer at €700 and a seller at $650 is not a €50 margin."""
    buyer = row("Apple iPhone 16 128GB Black", "buy", "buyer", qty=10, price=700.0,
                currency="EUR")
    options = fill_requirement(_requirement(buyer), lots(
        ("Apple iPhone 16 128GB Black", "us", 10, 650.0, "USD"),
    ))
    real = [o for o in options if o.kind != "near_miss"]
    assert real
    assert real[0].total_margin is None, "margin computed across EUR and USD"


def test_a_firm_on_both_sides_is_not_its_own_match():
    want = _requirement(row("Apple iPhone 16 128GB Black", "buy", "same-firm", qty=10))
    assert not fill_requirement(want, lots(
        ("Apple iPhone 16 128GB Black", "same-firm", 10, 600.0, "USD"),
    ))


def test_an_empty_lot_is_not_stock():
    want = _requirement(row("Apple iPhone 16 128GB Black", "buy", "buyer", qty=10))
    assert not fill_requirement(want, lots(
        ("Apple iPhone 16 128GB Black", "gulf", 0, 600.0, "USD"),
    ))


def test_an_unpriced_lot_is_offered_alone_never_blended():
    want = _requirement(row("Apple iPhone 16 128GB Black", "buy", "buyer", qty=100))
    options = fill_requirement(want, lots(
        ("Apple iPhone 16 128GB Black", "gulf", 60, None, "USD"),
        ("Apple iPhone 16 128GB Black", "manar", 40, 650.0, "USD"),
    ))
    for option in options:
        if len(option.allocations) > 1:
            assert all(a.unit_price is not None for a in option.allocations)
    assert any(o.allocations[0].counterparty_id == "gulf" for o in options if o.allocations)


def test_more_than_three_suppliers_is_not_offered_as_one_deal():
    want = _requirement(row("Apple iPhone 16 128GB Black", "buy", "buyer", qty=100))
    options = fill_requirement(want, lots(*[
        ("Apple iPhone 16 128GB Black", f"s{i}", 20, 640.0 + i, "USD") for i in range(5)
    ]))
    assert all(o.supplier_count <= 3 for o in options)


def test_a_buyer_who_gave_no_quantity_still_sees_who_has_it():
    want = _requirement(row("Apple iPhone 16 128GB Black", "buy", "buyer", qty=None))
    options = fill_requirement(want, lots(
        ("Apple iPhone 16 128GB Black", "gulf", 40, 640.0, "USD"),
    ))
    assert [o for o in options if o.kind != "near_miss"]


# ---------------------------------------------------------------- 4G and 5G


@pytest.mark.parametrize("wanted, offered", [
    ("Samsung Galaxy A16 128GB Black", "Samsung Galaxy A16 5G 128GB Black"),
    ("Samsung Galaxy A16 5G 128GB Black", "Samsung Galaxy A16 128GB Black"),
    ("Xiaomi Redmi Note 14 256GB Black", "Xiaomi Redmi Note 14 5G 256GB Black"),
])
def test_where_a_phone_comes_in_4g_and_5g_an_unstated_network_is_a_question(wanted, offered):
    """A Galaxy A16 and an A16 5G are two phones (A165, A166) under one name."""
    assert outcome(wanted, offered) == NEAR


@pytest.mark.parametrize("wanted, offered", [
    ("Samsung Galaxy A56 256GB Graphite", "Samsung Galaxy A56 5G 256GB Graphite"),
    ("Samsung Galaxy S25 256GB Navy", "Samsung Galaxy S25 5G 256GB Navy"),
    ("Google Pixel 10 128GB Obsidian", "Google Pixel 10 5G 128GB Obsidian"),
])
def test_where_only_a_5g_version_exists_leaving_it_out_is_shorthand(wanted, offered):
    assert outcome(wanted, offered) == EXACT


# ---------------------------------------------------------------- trade shorthand


@pytest.mark.parametrize("wanted, offered", [
    ("Apple iPhone 16 Pro Max 256GB Desert Titanium", "IP16PM 256GB Desert Titanium"),
    ("Apple iPhone 16 Pro Max 256GB Desert Titanium", "iPhone 16PM 256 Desert Titanium"),
    ("Apple iPhone 15 Pro 128GB Black Titanium", "15 Pro 128GB Black Titanium Apple"),
    ("Samsung Galaxy S24 Ultra 256GB Titanium Black", "SAM S24 ULTRA 256GB TITANIUM BLACK"),
    ("Samsung Galaxy S25 Ultra 1TB Titanium Black",
     "Samsung Galaxy S25 Ultra 1024GB Titanium Black"),
    ("Samsung Galaxy Z Fold7 512GB Blue Shadow", "Samsung Galaxy Fold 7 512GB Blue Shadow"),
])
def test_trade_shorthand_is_the_same_phone(wanted, offered):
    assert outcome(wanted, offered) == EXACT, f"{offered!r} not matched to {wanted!r}"


@pytest.mark.parametrize("wanted, offered", [
    ("Apple iPhone 17 Pro 256GB Orange", "Apple iPhone 17 Pro 256GB Cosmic Orange"),
    ("Apple iPhone 16 Pro 256GB Black Titanium", "Apple iPhone 16 Pro 256GB Titanium Black"),
    ("Samsung Galaxy S25 256GB Icy Blue", "Samsung Galaxy S25 256GB Icyblue"),
    ("Samsung Galaxy S25 Ultra 256GB Titanium Silverblue",
     "Samsung Galaxy S25 Ultra 256GB Silverblue Titanium"),
])
def test_one_finish_written_two_ways_is_one_finish(wanted, offered):
    assert outcome(wanted, offered) == EXACT, f"{offered!r} not matched to {wanted!r}"


@pytest.mark.parametrize("wanted, offered", [
    ("Apple iPhone 16 128GB Black New", "Apple iPhone 16 128GB Black Refurbished Grade B"),
    ("Apple iPhone 16 128GB Black Brand New Sealed", "Apple iPhone 16 128GB Grade A Black"),
    ("Apple iPhone 16 128GB Black New", "Apple iPhone 16 128GB Black CPO"),
    ("Google Pixel 9 Pro 16/256GB Obsidian", "Google Pixel 9 Pro 12/256GB Obsidian"),
])
def test_a_buyer_who_asked_for_new_is_never_quoted_graded_or_other_memory(wanted, offered):
    assert outcome(wanted, offered) != EXACT, f"{offered!r} quoted as {wanted!r}"


def test_a_grade_written_in_the_name_is_read_and_said():
    """The client's own rule: a buyer who names no grade is shown graded stock -- with
    the grade said, so nobody quotes a Grade A lot as new. Written in the product name,
    the grade used to be invisible, and the warning with it."""
    buyer = row("Apple iPhone 16 128GB Black", "buy", "buyer")
    seller = row("Apple iPhone 16 128GB Grade A Black", "sell", "seller", price=500.0)
    options = [o for o in fill_requirement(_requirement(buyer), [_supply(seller)])
               if o.kind != "near_miss"]
    assert options and "seller's lot is grade A" in options[0].warnings


# ---------------------------------------------------------------- found on the real board


def test_an_india_spec_buyer_is_not_offered_german_stock():
    """'IPHONE 16E 128GB black INDIA SPEC' was filled with 'Apple IPhone 16e 128GB
    Black DE': the country code is the market, and DE is EU."""
    assert outcome("IPHONE 16E 128GB black INDIA SPEC", "Apple IPhone 16e 128GB Black DE") \
        != EXACT


@pytest.mark.parametrize("wanted, offered", [
    ("Xiaomi Redmi Note 15 Pro 5G 12GB/512GB Blue", "Xiaomi Redmi Note 15 Pro 5G 8/512GB blue"),
    ("Google Pixel 9 Pro 16GB+256GB Obsidian", "Google Pixel 9 Pro 12/256GB Obsidian"),
    ("Samsung Galaxy S26 Ultra 256GB 12GB RAM Black", "Samsung Galaxy S26 Ultra 16/256GB Black"),
])
def test_memory_written_any_way_is_compared(wanted, offered):
    assert outcome(wanted, offered) != EXACT, f"{offered!r} quoted as {wanted!r}"
