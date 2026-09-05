"""Storage, colour, region code, grade, quantity and money."""

import pytest

from app.brain.normalise import (
    detect_incoterm,
    detect_price_basis,
    detect_vat_included,
    normalise_colour,
    normalise_currency,
    normalise_grade,
    normalise_region_code,
    normalise_storage_gb,
    parse_price,
    parse_quantity,
)

# ---------------------------------------------------------------- storage

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("256GB", 256),
        ("256 gb", 256),
        ("1TB", 1024),
        ("1 tb", 1024),
        ("2TB", 2048),
        ("512", 512),
        ("iPhone 15 Pro Max 128GB", 128),
    ],
)
def test_storage(raw, expected):
    assert normalise_storage_gb(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "40 pcs", "905 USD", "300GB", "grade A"])
def test_storage_rejects_implausible_sizes(raw):
    """'40 pcs' must not become a 40GB phone, and Apple never shipped 300GB."""
    assert normalise_storage_gb(raw) is None


# ---------------------------------------------------------------- colour

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("space grey", "Space Gray"),
        ("Space Gray", "Space Gray"),
        ("spc gray", "Space Gray"),
        ("SG", "Space Gray"),
        ("midnight", "Midnight"),
        ("Natural Titanium", "Natural Titanium"),
        ("nat titanium", "Natural Titanium"),
        ("titanium blue", "Blue Titanium"),
        ("deep purple", "Deep Purple"),
        ("  BLUE  ", "Blue"),
    ],
)
def test_colour(raw, expected):
    assert normalise_colour(raw) == expected


def test_unknown_colour_is_kept_not_nulled():
    """Colour is part of identity. Nulling an unrecognised one would collapse two
    genuinely different lots into a single identity, and the second import would
    overwrite the first."""
    assert normalise_colour("Cosmic Orange") == "Cosmic Orange"


# ---------------------------------------------------------------- region code

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LL/A", "LL/A"),
        ("ll/a", "LL/A"),
        ("LL / A", "LL/A"),
        ("LLA", "LL/A"),
        ("LL", "LL/A"),
        ("ZP/A", "ZP/A"),
        ("CH/A", "CH/A"),
        ("J/A", "J/A"),
        ("US spec", "LL/A"),
        ("UAE spec", "ZP/A"),
        ("Japan spec", "J/A"),
    ],
)
def test_region_code(raw, expected):
    assert normalise_region_code(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "Dubai", "shipping from Hong Kong", "grade A"])
def test_region_code_refuses_to_infer(raw):
    """A warehouse location is not a region code. Confusing a J/A unit — whose camera
    shutter cannot be silenced — with an LL/A one is a real customer dispute."""
    assert normalise_region_code(raw) is None


# ---------------------------------------------------------------- grade

ALIASES = {"a grade": "A", "aa": "A+", "14 day": "14-DAY", "cpo": "CPO", "as is": "AS-IS"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A grade", "A"),
        ("(A grade)", "A"),
        ("GRADE  A", "A"),
        ("AA", "A+"),
        ("14 day", "14-DAY"),
        ("CPO", "CPO"),
        ("as-is", "AS-IS"),
    ],
)
def test_grade_uses_the_clients_own_mapping(raw, expected):
    assert normalise_grade(raw, ALIASES) == expected


@pytest.mark.parametrize(("raw", "expected"), [("A+", "A+"), ("b", "B"), ("A +", "A+")])
def test_bare_grade_tokens_pass_through(raw, expected):
    assert normalise_grade(raw, ALIASES) == expected


@pytest.mark.parametrize("raw", [None, "", "mint condition", "very good", "BNIB", "refurb"])
def test_unmapped_grade_goes_to_review(raw):
    """Inventing a condition for goods worth hundreds a unit is not acceptable;
    None sends the row to a human, which costs seconds."""
    assert normalise_grade(raw, ALIASES) is None


# ---------------------------------------------------------------- quantity

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("40pcs", 40),
        ("40 pcs", 40),
        ("40 units", 40),
        ("qty: 40", 40),
        ("Quantity = 120", 120),
        ("40x iPhone 15", 40),
        ("iPhone 15 x40", 40),
        ("40", 40),
    ],
)
def test_quantity(raw, expected):
    assert parse_quantity(raw) == expected


def test_storage_is_never_read_as_a_quantity():
    """Reading 256 out of '256GB' as a count would promise a buyer stock that does
    not exist."""
    assert parse_quantity("iPhone 15 Pro Max 256GB") is None
    assert parse_quantity("40x iPhone 15 256GB") == 40


@pytest.mark.parametrize("raw", [None, "", "no stock", "0 pcs", "999999999 pcs"])
def test_quantity_rejects_nonsense(raw):
    assert parse_quantity(raw) is None


# ---------------------------------------------------------------- currency

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("905 USD", "USD"),
        ("USD 905", "USD"),
        ("AED 3300", "AED"),
        ("3300 dirhams", "AED"),
        ("€820", "EUR"),
        ("£710", "GBP"),
        ("905 dollars", "USD"),
    ],
)
def test_explicit_currency(raw, expected):
    assert normalise_currency(raw) == expected


def test_bare_dollar_sign_is_not_assumed_to_be_usd():
    """The most dangerous field in the system. '$' is USD in Dubai, SGD in Singapore,
    CAD in Toronto, AUD in Sydney — and a currency error does not look like an error,
    it looks like an unusually good margin."""
    assert normalise_currency("$905") is None


def test_bare_dollar_sign_resolves_from_the_sender_default():
    assert normalise_currency("$905", default="SGD") == "SGD"


def test_explicit_code_beats_the_sender_default():
    """A supplier who usually quotes AED but wrote USD today meant USD."""
    assert normalise_currency("905 USD", default="AED") == "USD"


# ---------------------------------------------------------------- price

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("905", 905.0),
        ("$905", 905.0),
        ("USD 905.50", 905.5),
        ("1,234.50", 1234.5),
        ("12,500", 12500.0),
    ],
)
def test_price(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "price on request", "TBC"])
def test_price_absent(raw):
    assert parse_price(raw) is None


# ---------------------------------------------------------------- price basis

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("905 per unit", "per_unit"),
        ("905 per pc", "per_unit"),
        ("905 each", "per_unit"),
        ("905/pcs", "per_unit"),
        ("36200 for the lot", "per_lot"),
        ("lot price 36200", "per_lot"),
        ("total price 36200", "per_lot"),
    ],
)
def test_price_basis(raw, expected):
    assert detect_price_basis(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "905 USD", "price 905"])
def test_price_basis_stays_unknown_when_unstated(raw):
    """A lot price read as a unit price inflates margin by the size of the lot — a
    hundredfold error that looks like the deal of the year."""
    assert detect_price_basis(raw) == "unknown"


# ---------------------------------------------------------------- incoterm & VAT

@pytest.mark.parametrize(
    ("raw", "expected"),
    [("EXW Dubai", "EXW"), ("price is DDP", "DDP"), ("FOB Hong Kong", "FOB")],
)
def test_incoterm(raw, expected):
    assert detect_incoterm(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "905 USD per unit"])
def test_incoterm_absent(raw):
    assert detect_incoterm(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("905 incl VAT", True),
        ("905 VAT included", True),
        ("905 ex VAT", False),
        ("905 excl. VAT", False),
        ("905 + VAT", False),
        ("905 USD", None),
        (None, None),
    ],
)
def test_vat(raw, expected):
    assert detect_vat_included(raw) is expected


# ------------------------------------------------- brands written the trade's way


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # Apple's watches, verbatim from a sample list. 'apple watch' needs both words;
        # the supplier writes neither the brand nor the space.
        ("Watch Ultra 3 LTE 49mm Black Titanium Case with Trail Loop", "Apple"),
        ("Watch Series 11 GPS + Cellular 46mm Natural TC", "Apple"),
        ("Beats Powerbeats Pro 2 - High-Performance Earbuds", "Apple"),
        # SanDisk sells by product line and rarely writes its own name.
        ("Ultra USB 3.0 Flash Drive", "SanDisk"),
        ("Cruzer Blade USB 2.0 Flash Drive", "SanDisk"),
        ("Ultra Dual Drive GO USB Type-C", "SanDisk"),
        # Sony's part codes are how their audio and phones are actually listed.
        ("WH-1000XM6 Headphone", "Sony"),
        ("XQ-GE74 12+512GB Xperia 1 VIII", "Sony"),
        ("Osmo Action 5 Pro Adventure Combo", "DJI"),
    ],
)
def test_brands_the_suppliers_never_name(description, expected):
    """1,407 rows on the real board had no brand and no barcode, so nothing downstream
    could match them. Most were not a comprehension problem — they were a catalogue gap,
    and four lines of pattern recovered 393 of them for no tokens and no latency."""
    from app.brain.normalise import detect_brand

    assert detect_brand(description) == expected


def test_watch_ultra_is_a_watch_and_ultra_flair_is_a_flash_drive():
    """The two patterns overlap on the word 'Ultra' and the order decides. Apple is
    listed first, so a flash drive named Ultra cannot be reached until Watch Ultra has
    had its turn — and a bare 'Ultra Flair' is still SanDisk."""
    from app.brain.normalise import detect_brand

    assert detect_brand("Watch Ultra 3 49mm") == "Apple"
    assert detect_brand("Ultra Flair USB 3.0") == "SanDisk"


def test_a_bare_watch_is_not_claimed_by_anyone():
    """'Watch' alone is far too broad — every smartwatch on the board would become an
    Apple. Each model line is named instead."""
    from app.brain.normalise import detect_brand

    assert detect_brand("Smart Watch D20 Fitness Tracker") != "Apple"
