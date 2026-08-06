"""Multi-brand product parsing and variant expansion.

Every input here is a real line, or a close paraphrase of one, from the 45 sample
emails. The old parser was iPhone-only and returned nothing for most of them.
"""

import pytest

from app.brain.normalise import expand_variants, parse_product

# ---------------------------------------------------------------- brand & category

@pytest.mark.parametrize(
    ("line", "brand", "category"),
    [
        ("Apple iPhone 14 128GB White", "Apple", "phone"),
        ("Samsung A566 Galaxy A56 5G 128GB Gray", "Samsung", "phone"),
        ("Google Pixel 10 256GB Blue EU", "Google", "phone"),
        ("telefon redmi note 14 pro 5g 8 256 gb midnight black", "Xiaomi", "phone"),
        ("Apple iPad Air 11 256GB", "Apple", "tablet"),
        ("Airpods PRO 3", "Apple", "audio"),
        ("Samsung Common 10,000mAh Power Bank", "Samsung", "accessory"),
        ("4smarts Pico Dual 20W Car Charger USB-C", "4smarts", "accessory"),
        ("Switch 2 Console Black", "Nintendo", "console"),
        ("PS5 825GB Digital Edition", "Sony", "console"),
        ("SINUDYNE 43\" DLED FHD TV", None, "tv"),
        ("Amazfit Balance Nylon", "Amazfit", "wearable"),
        ("Mask KN95 / FFP2", None, "other"),
    ],
)
def test_brand_and_category(line, brand, category):
    spec = parse_product(line)
    assert spec.brand == brand
    assert spec.category == category


@pytest.mark.parametrize(
    "line",
    [
        "A17 5G DS SM-A176 4+128 — Black — €135",
        "A35 5G DS A356B 6+128 Ent. Ed. — Awesome Navy — €195",
        "S25 S931B 5G DS 12+128 — Mint — €449",
        "Z Fold 7 5G F966 12+256 — Jet Black",
    ],
)
def test_samsung_recovered_from_part_codes(line):
    """Automic's list groups everything under a 'Samsung' heading and names no brand on
    the individual lines. The part code is the only clue left on the row."""
    assert parse_product(line).brand == "Samsung"


@pytest.mark.parametrize(
    "line",
    ["HP LaserJet M404 printer", "Article A356 spare part", "Model G556 bracket"],
)
def test_bare_codes_without_samsung_context_are_not_claimed(line):
    """The code pattern alone would attach Samsung to other brands' stock."""
    assert parse_product(line).brand != "Samsung"


def test_a_case_for_an_iphone_is_an_accessory_not_a_phone():
    """'A Good Case for iPhone 11' names an iPhone only to state compatibility.
    Filing it as a phone would put a €1.50 item on the board beside €900 handsets."""
    spec = parse_product("A Good Case for iPhone 11 Blueberry Blue")
    assert spec.category == "accessory"


# ---------------------------------------------------------------- capacity & RAM

@pytest.mark.parametrize(
    ("line", "ram", "capacity"),
    [
        # Samsung convention: RAM first, then storage
        ("A17 LTE DS SM-A175 8+256 Black", 8, 256),
        ("S25 Ultra S938B 5G DS 12+1TB Black", 12, 1024),
        ("A37 SM-A376B 5G 6+128 Charcoal", 6, 128),      # 6GB RAM is ordinary, 6GB storage is not
        # Plain capacity
        ("Apple iPhone 14 128GB White", None, 128),
        ("iPhone 17 Pro 1TB Silver", None, 1024),
        ("Samsung S931 Galaxy S25 256GB Navy", None, 256),
    ],
)
def test_ram_and_capacity(line, ram, capacity):
    spec = parse_product(line)
    assert spec.ram_gb == ram
    assert spec.capacity_gb == capacity


def test_accessories_have_no_capacity():
    assert parse_product("4smarts Pico Dual 20W Car Charger").capacity_gb is None


# ---------------------------------------------------------------- EAN identity

def test_ean_is_extracted_from_the_line():
    spec = parse_product("APPLE IPHONE 17 256GB WHITE 0195950643701")
    assert spec.ean == "0195950643701"


def test_ean_column_beats_the_line():
    spec = parse_product("Apple iPhone 17 256GB White", ean="0195950643701")
    assert spec.ean == "0195950643701"


def test_upc_and_ean_for_the_same_product_key_identically():
    """A US supplier sends a 12-digit UPC, a European one the 13-digit EAN, for the
    same physical product. Left unpadded they would never match each other — which
    defeats the entire point of using the barcode as identity."""
    upc = parse_product("Some product", ean="195950643701")
    ean = parse_product("Some product", ean="0195950643701")
    assert upc.identity_key() == ean.identity_key()


def test_fourteen_digit_padded_barcode_is_reduced():
    assert parse_product("x", ean="00195950643701").ean == "0195950643701"


def test_excel_apostrophe_prefix_is_stripped():
    """SELTE exports EANs as '6901443360147 so Excel does not mangle them."""
    assert parse_product("Smart fitness watch", ean="'6901443360147 ").ean == "6901443360147"


def test_identity_uses_ean_when_present():
    a = parse_product("APPLE IPHONE 17 256GB WHITE", ean="0195950643701")
    b = parse_product("Apple iPhone 17 White 256 GB", ean="0195950643701")
    assert a.identity_key() == b.identity_key() == "ean:0195950643701"


def test_identity_falls_back_to_spec_without_an_ean():
    spec = parse_product("Apple iPhone 14 128GB White")
    assert spec.identity_key().startswith("spec:")
    assert "apple" in spec.identity_key()


def test_two_capacities_are_different_products():
    a = parse_product("Apple iPhone 14 128GB White")
    b = parse_product("Apple iPhone 14 256GB White")
    assert a.identity_key() != b.identity_key()


def test_two_colours_are_different_products():
    a = parse_product("Apple iPhone 14 128GB White")
    b = parse_product("Apple iPhone 14 128GB Black")
    assert a.identity_key() != b.identity_key()


# ---------------------------------------------------------------- attributes

def test_network_dual_sim_and_edition():
    spec = parse_product("A56 5G DS SM-A566B 8+128 Ent. Ed. — Awesome Graphite")
    assert spec.network == "5G"
    assert spec.dual_sim is True
    assert spec.edition == "Enterprise Edition"


def test_never_raises_on_junk():
    for junk in [None, "", "   ", "???", "€€€", "1234567890" * 30]:
        parse_product(junk)


# ---------------------------------------------------------------- variant expansion

def test_colour_list_becomes_several_offers():
    """'Black / Blue / Grey — €125' is three offers at one price, not one offer."""
    variants = expand_variants("A17 LTE DS SM-A175 4+128 — Black / Blue / Grey — €125")
    assert len(variants) == 3
    assert {v.colour for v in variants} == {"Black", "Blue", "Grey"}
    assert all("A175" in v.text for v in variants)


def test_separately_priced_variants_are_split_and_keep_the_description():
    """'Lavender €200 · Charcoal €195' is two offers at two prices, and the second
    segment carries no product description of its own."""
    variants = expand_variants("A37 SM-A376B 5G 6+128 — Lavender €200 · Charcoal €195")
    assert len(variants) == 2
    assert all("A376B" in v.text for v in variants)
    assert "200" in variants[0].text
    assert "195" in variants[1].text


def test_three_colours_in_a_buyer_request():
    variants = expand_variants("IPHONE 15 128GB black / blue / pink")
    assert len(variants) == 3


def test_a_single_product_line_is_left_alone():
    variants = expand_variants("Apple iPhone 14 128GB White")
    assert len(variants) == 1
    assert variants[0].expanded_from_colours is False


@pytest.mark.parametrize(
    "line",
    [
        "iPhone 15 / iPhone 16 128GB",          # two models, not two colours
        "Blue / 256GB",                          # a colour and a capacity
        "Samsung A56 5G / LTE",                  # two network variants
    ],
)
def test_ambiguous_slashes_are_not_split(line):
    """Only runs where EVERY element is a colour get expanded. Splitting these would
    invent stock that was never offered — and invented stock gets promised to a buyer."""
    assert len(expand_variants(line)) == 1


def test_expansion_is_reflected_in_identity():
    """Each expanded colour must be a distinct offer, or two of the three overwrite
    the first during reconciliation."""
    variants = expand_variants("A17 LTE DS SM-A175 4+128 — Black / Blue / Grey — €125")
    keys = {parse_product(v.text).identity_key() for v in variants}
    assert len(keys) == 3
