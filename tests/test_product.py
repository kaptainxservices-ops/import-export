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


@pytest.mark.parametrize(
    "line",
    [
        # Screen protection, in the phrasings the corpus actually uses. Only the first
        # of these was ever matched; the rest reached the board filed as handsets.
        "Displex Privacy Glass for iPhone 14 = Displex 01707",
        "Displex Safety Glass for Samsung Galaxy S22+/S23+ = 01773",
        "PanzerGlass Classic Fit Screen Protection for Apple iPhone 15 Plus",
        "PanzerGlass Hoops Camera Lens Protector for Samsung Galaxy A35 5G",
        "QDOS OptiGuard 9H ECO Glass Plus for iPhone 15 / 16",
        "Max Mobile Temp Glass Full Glue for Xiaomi Redmi Note 10 5G Black",
        # Italian, from a real list. The product name beside it is in English, so with
        # no Italian vocabulary the line reads as an iPhone.
        "PROTEZIONE SCHERMO COMP.IPHONE 7/8 PLUS VETRO TEMP. (+ IVA)",
        # The general rule, for the accessory nobody has listed a word for yet.
        "Nudient Magnet Leather Wallet Midwinter Blue for iPhone 15",
    ],
)
def test_screen_protection_is_an_accessory_not_a_phone(line):
    """273 of 1,284 rows read as handsets were protectors naming the phone they fit.

    On a board sorted cheapest first that is what a trader sees before any real stock,
    and 'iPhone 15' at EUR 8.00 sitting above 'iPhone 15' at EUR 545.00 makes the
    cheapest-first sort useless for the one thing it is for.
    """
    assert parse_product(line).category == "accessory"


@pytest.mark.parametrize(
    "line",
    [
        # Every one of these carries a word the accessory rules look for — 'sim',
        # 'flip', 'glass' — and every one of them is a handset.
        "TCL SMARTPHONE TCL 5041 DUAL SIM DARK NIGHT GREY",
        "Google Pixel 10 Pro 5G Dual Sim 16GB RAM 128GB Obsidian DE",
        "SAMSUNG GALAXY Z FLIP 7 F766B 256GB 12GB BLUE SHADOW EU",
        "Samsung Galaxy S24 Ultra 512GB Gorilla Glass Victus Titanium Black",
        "Apple iPhone 15 Pro Max 256GB Natural Titanium",
    ],
)
def test_handsets_are_not_mistaken_for_accessories(line):
    """The mirror of the rule above, and the more expensive direction to get wrong.

    A protector filed as a phone is clutter; a EUR 900 handset filed as an accessory is
    stock that disappears from the board a trader is searching.
    """
    assert parse_product(line).category == "phone"


@pytest.mark.parametrize(
    "line",
    [
        "Looking for iPhone 15 128GB, 50 pcs",
        "WTB iPhone 16 Pro Max 256GB",
        "We need Samsung Galaxy S25 - 100 units",
    ],
)
def test_a_buyer_asking_for_a_phone_still_wants_a_phone(line):
    """'for iPhone' means compatibility on a seller's line and demand on a buyer's.

    Reading a WTB as an accessory would file real handset demand under the wrong
    category, which is worse than the leak the compatibility rule exists to close.
    """
    assert parse_product(line).category == "phone"


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


def test_colour_column_separates_products_the_description_cannot():
    """Masterfone lists colour in its own column and never repeats it in the
    description. Ignoring it collapsed 27% of that supplier's rows into duplicate
    identities, so two of every three finishes silently vanished into the first."""
    black = parse_product("Echo Spot 2024 speaker", colour="black")
    blue = parse_product("Echo Spot 2024 speaker", colour="blue")

    assert black.colour == "Black"
    assert black.identity_key() != blue.identity_key()


def test_colour_column_beats_the_description():
    spec = parse_product("iPhone 15 128GB Black", colour="Blue")
    assert spec.colour == "Blue"


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


@pytest.mark.parametrize(
    "line",
    [
        # Apple lists leads by what they connect and how long they are. The only word
        # on the row the rules used to recognise was 'iPhone'.
        "Iphone USB C to USB C MUF72ZM/A (1 Meter)",
        "Iphone USB C to USB C MLL82ZM/A (2 Meter)",
        "Apple USB-C to Lightning MX0K2ZM/A (1 m)",
    ],
)
def test_a_lead_that_never_says_cable_is_still_an_accessory(line):
    """Sorted cheapest first, these two sat at the very top of the phone board."""
    assert parse_product(line).category == "accessory"


def test_a_handset_that_charges_over_usb_c_is_still_a_handset():
    """The reason the cable rule matches connector-TO-connector rather than 'USB'.

    The iPhone 15 charges over USB-C, so a supplier will eventually put those letters on
    a handset row. When they do, it must not fall off the phone board.
    """
    assert parse_product("Apple iPhone 15 128GB Black USB-C").category == "phone"


@pytest.mark.parametrize(
    "line",
    [
        # Named by what they are made of, or what they do, and never 'case'.
        "Samsung Galaxy S25 FE Silicone, Black",
        "Samsung Galaxy A17 Clear, Transparent",
        "Samsung Galaxy S25 FE Standing Grip, Black",
        "Samsung Galaxy Z Fold7 Anti-reflecting Film, Transparent",
        "Galaxy A57 - Protective Film, Transparent",
        "Samsung Galaxy S25 Ultra - PA3 S Pen, Black",
        "iPhone 16/ 16 Pro/ 16 Pro Max /16 plus seal sticker",
        "iPhone CrossBody Strap",
    ],
)
def test_accessories_named_by_material_or_function(line):
    """Found by listing every word left on rows filed as phones once brands, models,
    capacities and colours were stripped out. Whatever survived that was the product.
    """
    assert parse_product(line).category == "accessory"


@pytest.mark.parametrize(
    "line",
    [
        "Redmi Pad 2 4+128GB",
        "TABLET REDMI PAD 2 128GB 4GB RAM MINT GREEN EU OEM",
        "Honor Magic Pad 4 wifi 12+256GB",
        "OnePlus Pad Go 2 12.1 5G 8GB RAM 256GB Shadow Black EU",
        "ULEFONE ARMOR PAD 3 PRO 10.36'' 4G 8/256GB BLACK",
    ],
)
def test_a_pad_is_a_tablet(line):
    """The tablet rule knew 'iPad', 'Tab' and 'tablet'. Everyone else calls theirs a
    Pad, so 109 tablets were sitting on the phone board."""
    assert parse_product(line).category == "tablet"


@pytest.mark.parametrize(
    "line",
    [
        # 'Box damaged' is a note about the packaging of a real handset.
        "SAMSUNG S948 GALAXY S26 ULTRA 256GB 12GB RAM 5G DS BLACK (BOX DANNEG)",
        # Transparent is a colour this handset is genuinely sold in, which is why the
        # 'Clear' rule requires a comma after it rather than matching either word.
        "Nothing Phone 2 256GB Transparent",
        "Samsung Galaxy A37 A376 5G Dual Sim 6GB RAM 128GB Awesome Charcoal DE",
    ],
)
def test_handsets_survive_the_accessory_vocabulary(line):
    assert parse_product(line).category == "phone"
