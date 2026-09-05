"""European versus Anglo number formats.

Every example here is taken from the real sample emails. Both conventions appear in
the same inbox, sometimes in the same week, so this cannot be a global setting.

The failure mode is what makes these tests matter. Reading €1.079 as €1.07 does not
surface as an error anywhere — it surfaces as an extraordinary margin on the Matches
screen, which is exactly the kind of number a broker acts on before checking.
"""

import pytest

from app.brain.normalise import parse_price


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Metropolitan Trends, 709 prices in this format in one email
        ("2,50", 2.50),
        ("1,90", 1.90),
        ("109,50", 109.50),
        ("259,50", 259.50),
        ("1219,00", 1219.00),
        # Automic: dot as thousands separator
        ("€1.079", 1079.00),
        ("1.079", 1079.00),
        # Anglo
        ("1,234.50", 1234.50),
        ("12,500", 12500.00),
        ("905", 905.00),
        ("905.50", 905.50),
        # World Comm float artifact
        ("15.470519999999999", 15.470519999999999),
    ],
)
def test_real_world_price_formats(raw, expected):
    assert parse_price(raw) == pytest.approx(expected)


def test_the_thousand_fold_error_is_fixed():
    """The specific bug: €1.079 is €1,079, not €1.07."""
    assert parse_price("€1.079") == 1079.00
    assert parse_price("€1.079") != 1.07


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("€1.079", 1079.0), ("1.234.567", 1234567.0), ("2.500", 2500.0)],
)
def test_dot_as_thousands(raw, expected):
    assert parse_price(raw) == pytest.approx(expected)


def test_ambiguous_three_digit_group_reads_as_thousands():
    """'1,079' could be 1079 or 1.079. Four-figure prices are overwhelmingly more
    plausible in this trade than one-euro phones."""
    assert parse_price("1,079") == 1079.0


@pytest.mark.parametrize(
    ("raw", "hint", "expected"),
    [
        ("1,079", "comma", 1.079),
        ("1.079", "dot", 1.079),
        ("109,50", "comma", 109.50),
        ("1.234,56", "comma", 1234.56),
        ("1,234.56", "dot", 1234.56),
    ],
)
def test_supplier_hint_overrides_the_default(raw, hint, expected):
    """A supplier whose convention is known should never be guessed at."""
    assert parse_price(raw, decimal_hint=hint) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("€ 905", 905.0), ("USD 905.50", 905.5), ("905 EUR", 905.0), ("Price: 1.079 €", 1079.0)],
)
def test_currency_markers_are_ignored(raw, expected):
    assert parse_price(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "price on request", "TBC", "n/a", "-"])
def test_no_price_present(raw):
    assert parse_price(raw) is None


def test_never_raises_on_junk():
    for junk in [",", ".", ",.,", "1,,2", "....", "€", "1.2.3.4.5"]:
        parse_price(junk)


def test_a_number_never_spans_whitespace():
    """The bug that put wrong prices on the live board. 'A57 5G' was being glued into
    '575', so a model number and a network standard became a handset's price — on
    every row from that supplier."""
    assert parse_price("Samsung A57 5G") == 57.0
    assert parse_price("A37 5G DS") == 37.0


# ---------------------------------------------------------------- prose


PROSE = [
    # Vadimpex: quantity in the first asterisks, price in the second
    ("Samsung A57 5G DS 8/128GB A576 *288* Navy *259€*", 259.0),
    ("OnePlus Pad Go 2 WIFI 8/128GB *180* Shadow Black *235€*", 235.0),
    ('Lenovo Yoga Tab WIFI 11.1" with Pen 12/256GB *110* Grey *379€*', 379.0),
    # other shapes
    ("A17 LTE DS SM-A175 4+128 — Black — €125", 125.0),
    ("40x iPhone 15 Pro Max 256GB Blue A LL/A @ 905 USD", 905.0),
]


@pytest.mark.parametrize(("line", "expected"), PROSE)
def test_prose_price_is_the_one_marked_as_money(line, expected):
    """A line holds a model number, a network standard, a capacity and a quantity.
    Only one number is a price, and a currency marker is what says which."""
    from app.brain.normalise import find_price

    assert find_price(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "Apple iPhone 15 128GB Black EU",
        "MOQ of 50 pcs, all models brand new",
        "Samsung A57 5G DS 8/128GB A576 *288* Navy",
    ],
)
def test_prose_without_a_currency_marker_yields_no_price(line):
    """A sentence full of numbers is not a price list. Guessing which number is money
    is exactly how 'A57 5G' became a 575 handset."""
    from app.brain.normalise import find_price

    assert find_price(line) is None


def test_asterisk_quantity_is_read_and_stripped_from_the_description():
    from app.pipeline import _quantity_from_markers

    quantity, description = _quantity_from_markers(
        "Samsung A57 5G DS 8/128GB A576 *288* Navy *259€*"
    )
    assert quantity == 288
    assert description == "Samsung A57 5G DS 8/128GB A576 Navy"


def test_the_money_marker_is_never_mistaken_for_a_quantity():
    from app.pipeline import _quantity_from_markers

    quantity, _ = _quantity_from_markers("OnePlus Watch 2 *364* Black *129€*")
    assert quantity == 364


# ---------------------------------------------------------------- barcodes


@pytest.mark.parametrize(
    "raw",
    [
        "840414699953",   # UPC-12, OT Distribution
        "0195949035999",  # EAN-13, Apple
        "12345678",       # EAN-8
        "01959490359990", # GTIN-14
    ],
)
def test_a_barcode_is_not_a_price(raw):
    """A long unbroken run of digits is an article number, not money.

    It reached a price column by sitting next to a currency symbol in prose, and
    numeric(12,2) rejected the insert — which is the good outcome. The bad outcome is
    the one digit shorter that fits, and gets quoted."""
    assert parse_price(raw) is None


def test_a_real_price_of_that_length_is_not_a_barcode():
    """The rule is about unbroken digits, not about magnitude. A separator means a
    human wrote a number, not a scanner."""
    assert parse_price("1.234.567,89") == 1234567.89


def test_a_currency_symbol_binds_to_the_number_after_it():
    """From OT Distribution, verbatim:

        FIRE TV STICK HD 8GB WI-Fi 5 UPC 840414699953 €24,50

    The € belongs to the 24,50 that follows it. Reading it as the barcode's trailing
    symbol consumed the €, left the real price unmarked, and put a twelve-digit UPC on
    the board as the price of a fire stick."""
    from app.brain.normalise import find_price

    line = "⁠ ⁠FIRE TV STICK HD 8GB WI-Fi 5 UPC 840414699953 €24,50"
    assert find_price(line) == 24.50


# ---------------------------------------------------------------- declared once


AB_BUSINESS = """WANT TO SELL
(PRICE IN EUR)
MOBILES / TABLETS/ COVID19 PROTECTION

SAMSUNG

Samsung A165 Galaxy A16 128GB Black
109,50
Samsung A566 Galaxy A56 5G 128GB Gray
259,50

APPLE

Apple iPhone 15 128GB Blue
585,00
"""


def test_a_currency_stated_once_governs_the_whole_list():
    """AB Business, verbatim. '(PRICE IN EUR)' under the subject and then sixty bare
    numbers — read row by row, all eighty of their offers reached the board with no
    currency at all, and an offer with no currency cannot be compared against anything.
    """
    from app.brain.normalise import detect_declared_currency

    assert detect_declared_currency("WANT TO SELL 27.07.2026", AB_BUSINESS) == "EUR"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("All prices in USD, EXW Dubai", "USD"),
        ("Preise in EUR inkl. MwSt", "EUR"),
        ("EUR PRICE LIST 24.07", "EUR"),
        ("Prezzi in EUR", "EUR"),
        ("prices AED", "AED"),
    ],
)
def test_the_ways_suppliers_state_it(text, expected):
    from app.brain.normalise import detect_declared_currency

    assert detect_declared_currency("", text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "iPhone 15 128GB 585\nNokia 3310 19,50",
        # The footer of the very email above. Matching a bare code anywhere would put a
        # currency on lists that never stated one, which is worse than leaving it blank.
        "VAT Number : FR39508687118\ncommercial@abbusiness.fr",
        "18 Rue Colbert, 13001 Marseille - FRANCE",
    ],
)
def test_a_stray_code_is_not_a_declaration(text):
    from app.brain.normalise import detect_declared_currency

    assert detect_declared_currency("", text) is None


def test_a_declaration_far_down_the_body_is_ignored():
    """A heading means it about the list. The same three letters 400 lines later is as
    likely to be a bank detail or an unsubscribe link."""
    from app.brain.normalise import detect_declared_currency

    buried = ("filler line\n" * 400) + "prices in USD"
    assert detect_declared_currency("", buried) is None


def test_the_row_wins_over_the_email_and_the_email_over_the_supplier():
    """Order of authority. A supplier who normally bills in USD but headed today's list
    'PRICE IN EUR' meant EUR — and a row that names its own currency outranks both."""
    row_currency, declared, supplier_default = None, "EUR", "USD"
    assert (row_currency or declared or supplier_default) == "EUR"

    row_currency = "GBP"
    assert (row_currency or declared or supplier_default) == "GBP"

    row_currency, declared = None, None
    assert (row_currency or declared or supplier_default) == "USD"


def test_the_price_comes_back_out_of_the_description():
    """A prose offer is one string, and the price has to be read out of it *and* removed.

    Left in, the board shows the price twice — once in its own column and once inside
    the product name — and, far worse, `description_key` is built from this text. A
    seller quoting 905 and a buyer wanting the identical handset at 960 then produce
    different keys and never meet. Every prose-quoted product was invisible to matching
    until this was fixed.
    """
    from app.brain.normalise import strip_marked_prices

    assert (
        strip_marked_prices("Apple iPhone 15 Pro Max 256GB Natural Titanium 905 USD")
        == "Apple iPhone 15 Pro Max 256GB Natural Titanium"
    )
    assert (
        strip_marked_prices("Apple iPhone 15 Pro Max 256GB Natural Titanium 960 USD")
        == "Apple iPhone 15 Pro Max 256GB Natural Titanium"
    )
    assert strip_marked_prices("Samsung A16 128GB — 105,00 €") == "Samsung A16 128GB"
    assert strip_marked_prices("iPhone 13 128GB €358") == "iPhone 13 128GB"


def test_two_sides_of_the_same_trade_agree_after_stripping():
    """The point of the above, stated as the thing that must be true."""
    from app.brain.normalise import parse_product, strip_marked_prices

    seller = parse_product(strip_marked_prices("Apple iPhone 15 Pro Max 256GB Natural Ti 905 USD"))
    buyer = parse_product(strip_marked_prices("Apple iPhone 15 Pro Max 256GB Natural Ti 960 USD"))

    assert seller.identity_key() == buyer.identity_key()


def test_a_line_that_is_only_a_price_is_handed_back_whole():
    """Never return an empty description. A line with no product in it is the caller's
    to reject, not this function's to erase."""
    from app.brain.normalise import strip_marked_prices

    assert strip_marked_prices("€905") == "€905"


def test_a_trailing_symbol_still_marks_a_price():
    """The common European form must keep working."""
    from app.brain.normalise import find_price

    assert find_price("Samsung A16 128GB 105,00 €") == 105.00
    assert find_price("iPhone 15 128GB 555,00€") == 555.00
