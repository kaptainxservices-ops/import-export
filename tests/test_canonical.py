"""One name per model, however the supplier wrote it -- and never one name for two.

Both lists matter equally. A pair on the first that fails to meet is a buyer the board
cannot fill; a pair on the second that meets is a buyer offered the wrong phone.
"""

import pytest

from app.brain.normalise.canonical import (
    canonical_brand,
    canonical_model,
    differs_only_in_description,
)

SAME = [
    ("SAM S23 128G Black", "Samsung Galaxy S23 5G 8/128GB"),
    ("samsung s23", "SAM s23"),
    ("Samsung S23", "Galaxy S23"),
    ("Galaxy S24+ 256GB", "SAM S24 PLUS"),
    ("Samsung Galaxy S24 Ultra", "SAMSUNG S24ULTRA"),
    ("S24U 256GB", "Galaxy S24 Ultra"),
    ("Galaxy Z Flip7 FE", "SAM FLIP 7FE"),
    ("Galaxy Z Fold 7 512GB", "SAM FOLD 7"),
    ("S25FE 5G 128GB 8GB (SM-S731/DS) NEW", "SAM S25FE"),
    ("SAM S25E", "Samsung Galaxy S25 Edge 256GB"),
    ("Samsung S24", "Samsung Galaxy S24 5G Dual Sim"),
    ("GALAXY WATCH ULTRA (SM-L705F) NEW", "Samsung Watch Ultra"),
    ("Galaxy Tab S10 Lite", "Samsung Tab S10 Lite WiFi"),
    ("Galaxy Buds3 Pro", "Samsung Galaxy Buds 3 Pro"),
    ("IP15PM 256", "Apple iPhone 15 Pro Max 256GB Blue"),
    ("Apple iPhone 16 Pro Max 256GB Desert Titanium", "IPHONE 16 PROMAX 256"),
    ("iPhone 16 Plus", "IP16+"),
    ("Apple iPhone 16e 128GB", "iphone 16 e 128gb"),
    ("APPLE IPHONE 18 PRO 5G 256GB (A3714) NEW", "iphone 18 pro"),
    ("Google Pixel 9 Pro 128GB", "Pixel 9 Pro"),
    ("Xiaomi 14T Pro", "XIAOMI 14T PRO 5G 12/512"),
    ("Xiaomi Redmi Note 14 Pro 5G", "Redmi Note 14 Pro"),
    ("OnePlus 15R", "ONEPLUS 15R 256GB 12GB (CPH2767)"),
    ("ONEPLUS NORD CE6 256GB 8GB (CPH2807) NEW PITCH BLACK", "OnePlus Nord CE6"),
    ("MOTOROLA MOTO G06 5G 64GB 4GB (XT2535-3) NEW", "Motorola G06"),
    ("Moto G37 Power", "MOTOROLA MOTO G37 POWER (XT2625-6) NEW"),
    ("HONOR MAGICPAD4 WiFi 512GB 16GB (YLE-W09) NEW", "HONOR MAGIC PAD 4 WIFI 256GB 12GB"),
]

DIFFERENT = [
    ("Samsung S23", "Samsung S23 Ultra"),
    ("Samsung S23", "Samsung S23 FE"),
    ("Samsung S23", "Samsung S23+"),
    ("SAM S24", "SAM S24 PLUS"),
    ("S25", "S25E"),
    ("S25 FE", "S25E"),
    ("SAM FOLD 7", "SAM FLIP 7"),
    ("SAM FLIP 7", "SAM FLIP 7FE"),
    ("SAM FOLD 6", "SAM FOLD 7"),
    ("Galaxy A56", "Galaxy A55"),
    ("Galaxy Tab S10", "Galaxy Tab S10 Lite"),
    ("Galaxy Tab S10", "Galaxy S10"),
    ("Galaxy Watch Ultra", "Galaxy Watch 8"),
    ("iPhone 15", "iPhone 15 Pro"),
    ("iPhone 15 Pro", "iPhone 15 Pro Max"),
    ("iPhone 16", "iPhone 16e"),
    ("iPhone 16", "iPhone 16 Plus"),
    ("Pixel 9", "Pixel 9 Pro"),
    ("Pixel 9 Pro", "Pixel 9 Pro XL"),
    ("Redmi Note 14", "Redmi Note 14 Pro"),
    ("Redmi Note 14 Pro", "Redmi Note 14 Pro+"),
    ("Moto G37", "Moto G37 Power"),
    ("Xiaomi 14T", "Xiaomi 14T Pro"),
    ("OnePlus Nord CE6", "OnePlus Nord CE5"),
    ("OnePlus Nord CE5 Sandstone Magnetic Case", "OnePlus Nord CE5"),
    ("HONOR MAGICPAD4 WiFi", "HONOR MAGICPAD4 SMART KEYBOARD"),
    ("Honor Pad 20", "Honor Pad 20 Pro"),
    ("Honor Magic Pencil 3", "Honor Magic Pencil 4S"),
    ("Samsung S23", "Apple iPhone 15"),
]


@pytest.mark.parametrize(("a", "b"), SAME)
def test_one_model_written_two_ways_is_one_key(a, b):
    assert canonical_model(a).key == canonical_model(b).key != ""


@pytest.mark.parametrize(("a", "b"), DIFFERENT)
def test_two_models_never_share_a_key(a, b):
    assert canonical_model(a).key != canonical_model(b).key


def test_a_brand_column_supplies_a_brand_the_line_leaves_out():
    assert canonical_model("X6b-128GB 4GB", brand="HONOR").key == "honor:x6b"
    assert canonical_model("PAD X9A-WIFI 128GB 6GB (ELN2-W29) NEW", brand="HONOR").key == (
        "honor:padx9a"
    )


def test_a_samsung_number_with_no_brand_is_samsung():
    model = canonical_model("S23 Ultra 256GB")
    assert (model.brand, model.category) == ("Samsung", "phone")


def test_network_and_wifi_are_attributes_not_part_of_the_name():
    four, five = canonical_model("Samsung A16 4G"), canonical_model("Samsung A16 5G")
    assert four.key == five.key
    assert (four.network, five.network) == ("4g", "5g")
    assert canonical_model("Galaxy Tab S10 Lite WiFi").connectivity == "wifi"
    assert canonical_model("Galaxy Tab S10 Lite 5G").connectivity == "cellular"


def test_colour_column_words_never_reach_the_name():
    model = canonical_model("MOTO G06 PANTONE TAPESTRY", colour="PANTONE TAPESTRY (BLUE)")
    assert model.key == "motorola:g06"


@pytest.mark.parametrize(
    ("cell", "brand"),
    [("SAMSUNG", "Samsung"), ("Sam", "Samsung"), ("XIOAMI", "Xiaomi"), ("HUAWEI", "Huawei"),
     ("HONOR", "Honor"), ("RAY-BAN", "Ray-Ban"), ("SANDISK", "SanDisk"), ("DJI", "DJI"),
     ("ARTIX", "Artix"), ("", None), ("BRAND NEW TABLET", None)],
)
def test_brand_cells(cell, brand):
    assert canonical_brand(cell) == brand


def test_only_descriptive_leftovers_may_be_asked_about():
    assert differs_only_in_description(("nord", "ce6", "pitch"), ("nord", "ce6"))
    assert not differs_only_in_description(("s23", "ultra"), ("s23",))
    assert not differs_only_in_description(("s23",), ("s24",))
    assert not differs_only_in_description(("nord", "ce5", "case"), ("nord", "ce5"))
    assert not differs_only_in_description(("wireless", "headphones"), ("headphones",))


def test_nothing_recognisable_gives_no_key_and_never_raises():
    assert canonical_model("").key == ""
    assert canonical_model(None).key == ""
    assert canonical_model("((()))").key == ""
