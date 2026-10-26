"""Matching across the ways suppliers and buyers write the same model.

Built from board rows through the same adapters the /matches endpoints use, so what is
tested is what the dashboard gets: a buyer's 'Samsung S23 128GB' against a supplier's
'SAM S23 | 128G | Black', with the canonical model, the attribute checks and any stored
alias all in play.
"""

from app.api.matches import _requirement, _supply
from app.brain.matching import fill_requirement, place_offer
from app.db.models import BoardOffer


def row(
    row_id, description, side, *, brand=None, capacity=None, colour=None, qty=10,
    price=None, grade=None, region=None, availability=None, note=None, ram=None,
    edition=None, cp=None, category=None, ean=None,
):
    return BoardOffer(
        id=row_id, counterparty_id=cp or f"cp-{row_id}", counterparty_name=None, side=side,
        description=description, quantity=qty, unit_price=price, currency="GBP",
        identity_key=f"spec:{row_id}", match_key=None, description_key=None, ean=ean,
        brand=brand, capacity_gb=capacity, colour=colour, category=category, grade=grade,
        region_code=region, ram_gb=ram, edition=edition, availability=availability,
        availability_note=note,
    )


def fills(want, *supply, aliases=None):
    options = fill_requirement(_requirement(want, aliases), [_supply(s, aliases) for s in supply])
    return [o for o in options if o.kind != "near_miss"], [
        o for o in options if o.kind == "near_miss"
    ]


def test_sam_s23_meets_samsung_galaxy_s23():
    """The example the client gave: names written differently were not matching."""
    want = row("w", "Samsung Galaxy S23 128GB Black", "buy", brand="Samsung", capacity=128,
               colour="Black", price=200)
    have = row("h", "SAM S23", "sell", brand="Samsung", capacity=128, colour="Black",
               price=185, grade="B")
    real, _ = fills(want, have)
    assert len(real) == 1
    assert real[0].total_margin == 150.0


def test_an_s23_never_meets_an_s23_ultra_or_fe():
    want = row("w", "Samsung S23 128GB", "buy", capacity=128, colour="Black")
    ultra = row("u", "SAM S23 ULTRA", "sell", brand="Samsung", capacity=128, colour="Black",
                price=300)
    fe = row("f", "SAM S23FE", "sell", brand="Samsung", capacity=128, colour="Black", price=150)
    real, near = fills(want, ultra, fe)
    assert real == [] and near == []


def test_a_buyer_who_names_no_colour_takes_any_colour():
    want = row("w", "Samsung S24 FE 128GB x50", "buy", capacity=128, qty=50)
    blue = row("b", "SAM S24FE", "sell", brand="Samsung", capacity=128, colour="Blue", qty=30,
               price=255)
    mint = row("m", "SAM S24FE", "sell", brand="Samsung", capacity=128, colour="Mint", qty=20,
               price=255)
    real, _ = fills(want, blue, mint)
    assert any(o.filled_quantity == 50 for o in real)


def test_the_wrong_grade_is_a_near_miss_and_says_so():
    want = row("w", "SAM S24 128GB Black grade A", "buy", capacity=128, colour="Black",
               grade="A")
    c_grade = row("c", "SAM S24", "sell", brand="Samsung", capacity=128, colour="Black",
                  grade="C", price=210)
    real, near = fills(want, c_grade)
    assert real == []
    assert near[0].relaxed == ["grade C, wanted A"]


def test_a_buyer_asking_for_new_is_told_the_lot_is_graded():
    want = row("w", "Samsung S24 128GB Black NEW sealed", "buy", capacity=128, colour="Black")
    graded = row("g", "SAM S24", "sell", brand="Samsung", capacity=128, colour="Black",
                 grade="B", price=290)
    real, near = fills(want, graded)
    assert real == []
    assert "wanted new" in near[0].relaxed[0]


def test_a_buyer_who_states_no_grade_still_sees_the_grade():
    want = row("w", "Samsung S24 128GB Black", "buy", capacity=128, colour="Black")
    graded = row("g", "SAM S24", "sell", brand="Samsung", capacity=128, colour="Black",
                 grade="AA", price=310)
    real, _ = fills(want, graded)
    assert "seller's lot is grade AA" in real[0].warnings


def test_a_different_market_is_a_near_miss():
    want = row("w", "OnePlus 15R 256GB EU spec", "buy", capacity=256, colour="Charcoal Black",
               region="EU")
    india = row("i", "ONEPLUS 15R 256GB 12GB (CPH2767)", "sell", brand="OnePlus", capacity=256,
                colour="Charcoal Black", region="INDIA", price=340)
    real, near = fills(want, india)
    assert real == [] and near[0].relaxed == ["spec INDIA, wanted EU"]


def test_4g_and_5g_of_one_model_are_different_phones():
    want = row("w", "Samsung Galaxy A16 5G 128GB Black", "buy", capacity=128, colour="Black")
    four = row("f", "Samsung A16 4G 128GB Black", "sell", capacity=128, colour="Black",
               price=100)
    real, near = fills(want, four)
    assert real == [] and near[0].relaxed == ["4G, wanted 5G"]


def test_different_memory_is_a_near_miss():
    want = row("w", "Xiaomi 14T Pro 12/512 Black", "buy", capacity=512, colour="Black", ram=12)
    have = row("h", "Xiaomi 14T Pro 16/512 Black", "sell", capacity=512, colour="Black", ram=16,
               price=500)
    real, near = fills(want, have)
    assert real == [] and near[0].relaxed == ["16GB RAM, wanted 12GB"]


def test_incoming_stock_fills_but_says_when_it_arrives():
    want = row("w", "JBL Wave Flex 2 Blue", "buy", colour="Blue", qty=20)
    have = row("h", "JBL WAVE FLEX 2 (JBLWFLEX2BLK) NEW", "sell", brand="JBL", colour="Blue",
               qty=23, price=27, availability="incoming", note="ETA 7-15 DAYS")
    real, _ = fills(want, have)
    assert "incoming stock (ETA 7-15 DAYS)" in real[0].warnings


def test_a_lot_with_no_price_is_offered_as_a_lead():
    want = row("w", "JBL Wave Beam 2 Black", "buy", colour="Black", qty=500)
    have = row("h", "JBL WAVE BEAM 2 EARBUDS (JBLWBEAM2BLK) NEW", "sell", brand="JBL",
               colour="Black", qty=1114, price=None, availability="incoming")
    real, _ = fills(want, have)
    assert real and real[0].allocations[0].unit_price is None
    assert "seller listed no price; ask for one" in real[0].warnings


def test_an_unrecognised_extra_word_is_a_near_miss_until_settled():
    """'Nord CE6 Smooth' against 'Nord CE6': probably the same, shown for a person to
    confirm -- and once the resolver has settled it, an ordinary fill."""
    want = row("w", "OnePlus Nord CE6 Smooth 256GB", "buy", capacity=256)
    have = row("h", "ONEPLUS NORD CE6 256GB 8GB (CPH2807) NEW", "sell", brand="OnePlus",
               capacity=256, colour="Black", price=236)

    real, near = fills(want, have)
    assert real == [] and "check it is the same model" in near[0].relaxed[0]

    real, near = fills(want, have, aliases={"oneplus:nordce6smooth": "oneplus:nordce6"})
    assert len(real) == 1 and near == []


def test_an_accessory_never_fills_the_device_it_fits():
    want = row("w", "Honor MagicPad 4 256GB", "buy", capacity=256, category="tablet")
    keyboard = row("k", "HONOR MAGICPAD4 SMART KEYBOARD (YONGLE-KEYBOARD00B) NEW", "sell",
                   brand="Honor", colour="Dark Gray", price=30, category="accessory")
    real, near = fills(want, keyboard)
    assert real == [] and near == []


def test_placing_a_lot_uses_the_same_rules():
    lot = _supply(row("h", "SAM FLIP 7FE", "sell", brand="Samsung", capacity=256,
                      colour="Black", qty=31, price=415))
    buyers = [
        _requirement(row("b1", "Samsung Galaxy Z Flip7 FE 256GB Black", "buy", capacity=256,
                         colour="Black", qty=10, price=450)),
        _requirement(row("b2", "Samsung Galaxy Z Flip 7 256GB Black", "buy", capacity=256,
                         colour="Black", qty=10, price=500)),
    ]
    options = place_offer(lot, buyers)
    assert {a.offer_id for o in options for a in o.allocations} == {"b1"}


def test_a_samsung_part_code_outside_brackets_is_not_part_of_the_name():
    want = row("w", "Samsung Galaxy S25 Ultra 256GB S938 Black EU", "buy", capacity=256,
               colour="Black", region="EU")
    have = row("h", "Samsung Galaxy S25 Ultra (S938)/5G/DS/256GB Black EU", "sell",
               brand="Samsung", capacity=256, colour="Black", region="EU", price=900)
    real, _ = fills(want, have)
    assert len(real) == 1


def test_an_apple_part_number_says_which_market_the_unit_is_for():
    """An India-spec order was being offered a European MFYP4ZD/A unit."""
    from app.brain.normalise import parse_product

    europe = parse_product("Apple iPhone 17 Pro Max 256GB Blue, Model No. A3526 - MFYP4ZD/A")
    india = parse_product("Apple iPhone 15 128GB Black, Model No. A3090 - MTP03HX/A")
    assert (europe.region_code, india.region_code) == ("ZD/A", "HX/A")

    want = row("w", "IPHONE 17 PRO MAX 256GB blue INDIA SPEC", "buy", capacity=256,
               colour="Blue", region="INDIA")
    eu_unit = row("e", europe.description, "sell", brand="Apple", capacity=256, colour="Blue",
                  region="ZD/A", price=1000)
    in_unit = row("i", "Apple iPhone 17 Pro Max 256GB Blue HN/A", "sell", brand="Apple",
                  capacity=256, colour="Blue", region="HN/A", price=1010)
    silent = row("s", "Apple iPhone 17 Pro Max 256GB Blue", "sell", brand="Apple",
                 capacity=256, colour="Blue", price=990)
    real, near = fills(want, eu_unit, in_unit, silent)
    assert {o.allocations[0].offer_id for o in real} == {"i", "s"}
    assert near[0].relaxed == ["spec ZD/A, wanted INDIA"]
    unstated = next(o for o in real if o.allocations[0].offer_id == "s")
    assert "seller did not state the spec; buyer wants INDIA" in unstated.warnings


def test_an_unlabelled_line_beside_a_5g_line_of_the_same_model_is_the_4g_phone():
    from app.api.matches import infer_networks

    want = row("w", "Samsung Galaxy A17 5G 128GB Blue", "buy", capacity=128, colour="Blue")
    four = row("4", "Samsung Galaxy A17 (A175)/DS/128GB Blue EU", "sell", brand="Samsung",
               capacity=128, colour="Blue", price=120, cp="automic")
    five = row("5", "Samsung Galaxy A17 (A176)/5G/DS/128GB Blue EU", "sell", brand="Samsung",
               capacity=128, colour="Blue", price=135, cp="automic")
    supply = infer_networks([_supply(four), _supply(five)])
    options = fill_requirement(_requirement(want), supply)
    exact = {o.allocations[0].offer_id for o in options if o.kind == "exact"}
    assert exact == {"5"}
