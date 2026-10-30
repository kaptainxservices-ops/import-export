"""Formats from the client's real inbox that used to read as nothing.

Each of these sent a whole email to Review as "nothing could be read" -- and, for a
buyer's list, took that buyer's demand off the match board entirely. The lines are as
the senders wrote them.
"""

import pytest

from app.brain.normalise import find_price, parse_quantity
from app.brain.sender import resolve_sender
from app.brain.tables.parser import parse_grid
from app.brain.tables.reader import Grid
from app.pipeline import _requests_from_prose

# ---------------------------------------------------------------- quantities


@pytest.mark.parametrize("cell, expected", [
    ("200x", 200), ("400 x", 400), ("x200", 200),       # Phoneport's whole wanted list
    ("1k", 1000), ("2.5k", 2500), ("1,5k", 1500),
    ("1,000", 1000), ("1.000", 1000), ("12 000", 12000),
    ("50+", 50), (">50", 50),
    ("40", 40), ("40pcs", 40), ("qty: 40", 40),
])
def test_quantities_as_people_write_them(cell, expected):
    assert parse_quantity(cell) == expected


@pytest.mark.parametrize("text", [
    "X130 128GB", "Moto X30 Pro 256GB", "iPhone 15 256GB", "2x2 MIMO", "Galaxy S25+",
    "0", "Apple Watch 45mm",
])
def test_model_codes_and_sizes_are_not_quantities(text):
    """'X130 128GB' was 130 units: a model code read as a count is stock that does not
    exist, promised to a buyer by combination matching."""
    assert parse_quantity(text) is None


# ---------------------------------------------------------------- prices


@pytest.mark.parametrize("line, price", [
    ("AirPods 4 USB-C Charging Case (MXP63LL/A) 200x 95,- EUR", 95.0),   # CyTec
    ("Pixel 9a 128GB obsidian 250x 291,- EUR", 291.0),
    ("Switch 2, gray/gray, DE 250x 382,–€", 382.0),
    ("3 units IPHONE 15 256GB BLACK -590€", 590.0),                     # Cellular Iberia
    ("FIRE TV STICK HD 8GB WI-Fi 5 UPC 840414699953 €24,50", 24.5),
])
def test_prices_as_people_write_them(line, price):
    assert find_price(line) == price


def test_a_count_before_a_german_price_is_the_quantity():
    assert parse_quantity("AirPods 4 USB-C Charging Case (MXP63LL/A) 200x 95,- EUR") == 200


# ---------------------------------------------------------------- tables


def test_a_sellers_item_number_is_not_the_product():
    """HITISY: 'Item No. | Model | EAN | EUR'. 'Item' mapped the numbers to description
    and every row was thrown away as a bare number -- 161 lines read as none."""
    grid = Grid(rows=[
        ["Item No.", "Model", "EAN", "EUR"],
        ["7318", "Apple iPhone 16 128GB - White", "195949822148", "616,00"],
        ["9222", "Apple iPhone 17 256GB - Black", "195950643503", "759,00"],
        ["8601", "Apple iPhone 17 Pro 256GB - Cosmic Orange", "195950627442", "1.007,00"],
    ])
    rows = parse_grid(grid).rows
    assert [r.description for r in rows][0] == "Apple iPhone 16 128GB - White"
    assert [r.price for r in rows] == [616.0, 759.0, 1007.0]


def test_a_wanted_list_with_section_labels_in_the_header():
    """Phoneport: the header's first cell is a section label ('Apple Iphone'), the
    quantities are '200x' and '1k', and there are no prices at all."""
    grid = Grid(rows=[
        ["Want to buy / Gesuche"],
        ["Apple Iphone", "QTY/Menge", "QTY/Menge", "Price/Preis", "Delivery Date/LT"],
        ["iPhone Air 1TB all colors", "200x", "", "", ""],
        ["iPhone 16 128GB Black ZD/A", "1k", "", "", ""],
        ["Google Pixel", "", "", "", ""],
        ["Google Pixel 10 Pro 128GB all colors", "300x", "", "", ""],
    ])
    rows = parse_grid(grid).rows
    assert [(r.description, r.quantity) for r in rows] == [
        ("iPhone Air 1TB all colors", 200),
        ("iPhone 16 128GB Black ZD/A", 1000),
        ("Google Pixel 10 Pro 128GB all colors", 300),
    ]


# ---------------------------------------------------------------- buyer requests


def test_a_wanted_list_written_as_lines():
    """New Way International, retyped: counts set apart by a wide gap, no prices."""
    body = (
        "WTB\n"
        " 2           Google Pixel 10 128GB Frost\n"
        "17          Google Pixel 10 256GB Indigo\n"
        "S25 256GB Icy Blue\n"
        "X130 128GB\n"
        "200  units of Motorola Moto G55 5G XT2435-2 Smoky Green 8/256gb\n"
        "Chandan Bharati\n"
        "TVD Services B.V.\n"
        "Beursplein 37,3011AA, Rotterdam\n"
    )
    rows = [(r.description, r.quantity) for r in _requests_from_prose(body)]
    assert rows == [
        ("Google Pixel 10 128GB Frost", 2),
        ("Google Pixel 10 256GB Indigo", 17),
        ("S25 256GB Icy Blue", None),
        ("X130 128GB", None),
        ("Motorola Moto G55 5G XT2435-2 Smoky Green 8/256gb", 200),
    ]


def test_brand_headings_carry_down_and_a_bare_number_is_not_a_count():
    """Bauer: '16 128gb' under 'Apple' is an iPhone 16 -- not sixteen of something."""
    body = (
        "WTB\nApple\n16 128gb mix colours\n17 256gb mix colours\n"
        "All specs welcomed if not specifically indicated\n"
        "Samsung\ns26 ultra 5g 256gb violet\n"
        "Kind regards\nSomeone\n+49 123 456 789 0\n"
    )
    rows = [(r.description, r.quantity, r.brand) for r in _requests_from_prose(body)]
    assert rows == [
        ("Apple iPhone 16 128gb mix colours", None, "Apple"),
        ("Apple iPhone 17 256gb mix colours", None, "Apple"),
        ("Samsung s26 ultra 5g 256gb violet", None, "Samsung"),
    ]


# ---------------------------------------------------------------- senders


def test_an_outlook_forward_with_the_signature_on_top():
    """Cellular Iberia, forwarded by Sameer: no 'forwarded message' marker, and an
    800-character signature above the supplier's header."""
    signature = "Sameer Shah\nTVD Services B.V.\n" + ("Beursplein 37, Rotterdam\n" * 40)
    body = (
        signature
        + "From: CELLULAR IBERIA <export@cellular-iberia.com>\n"
        + "Reply to: export@cellular-iberia.com\n"
        + "Date: Monday, 27 July 2026 at 10:31\n"
        + "To: tvdservices@hotmail.com\n"
        + "Subject: CELLULAR IBERIA OFFER 27/07/2026\n\nToday i can offer you:\n"
    )
    sender = resolve_sender(
        "tvdservices@hotmail.com", "FW: CELLULAR IBERIA OFFER", body,
        internal_domains={"tvdservices.com"}, internal_addresses={"tvdservices@hotmail.com"},
    )
    assert sender.email == "export@cellular-iberia.com"
    assert not sender.needs_review


def test_a_from_line_in_running_text_is_not_a_forward():
    body = "Hi,\nFrom: our side all is fine, contact sales@somebody.example for prices.\n"
    sender = resolve_sender(
        "sales@tvdservices.com", "Hello", body, internal_domains={"tvdservices.com"},
    )
    assert sender.method != "forwarded_header"


def test_an_availability_list_is_not_twenty_review_items():
    """Landotech: '200+ pcs', no prices anywhere. That is the list's format, said once
    about the list -- not a 'row with no price' on each of its 21 lines."""
    from app.brain.confidence import score_row

    in_a_priced_list = score_row(description="Motorola Moto G06 4/64GB Arabesque", price=None,
                                 quantity=200, side="sell", list_has_prices=True)
    in_an_unpriced_list = score_row(description="Motorola Moto G06 4/64GB Arabesque",
                                    price=None, quantity=200, side="sell",
                                    list_has_prices=False)

    assert in_a_priced_list.needs_review
    assert not in_an_unpriced_list.needs_review
    assert "the list states no prices" in in_an_unpriced_list.list_reasons


def test_a_newsletter_table_with_a_table_nested_in_its_header():
    """MACWAY, every day: the header cell holds a tiny nested table, which used to add
    its cells to the header -- five columns over three-column rows, quantity and price
    pointing past the end of every row -- and 'Brand New Stock' read as a Brand column.
    The section headings say each block's condition and market."""
    from app.brain.tables.reader import read_html_tables

    html = """
    <table>
      <tr><td><table><tr><td>Brand New Stock (UK Spec)</td></tr></table>
              Brand New Stock (UK Spec)</td>
          <td>Qty</td><td>Price</td></tr>
      <tr><td>Apple iPhone 17 Pro 256GB - Blue, Orange</td><td>40</td><td>Ask</td></tr>
      <tr><td>Apple iPhone 17 Pro Max 256GB - Orange, Blue</td><td>50</td><td>Ask</td></tr>
      <tr><td>CPO &amp; ASIS (Non EU - Spec)</td><td>Qty</td><td>Price</td></tr>
      <tr><td>Apple iPhone 13 Pro 256GB (CPO) - Mix Colors</td><td>50</td><td>Ask</td></tr>
      <tr><td>Apple iPhone 16 Pro Max 256GB (CPO) - Mix Colors</td><td>50</td><td>Ask</td></tr>
    </table>"""
    rows = parse_grid(read_html_tables(html)[0]).rows
    assert [(r.description[:24], r.quantity, r.region, r.grade) for r in rows] == [
        ("Apple iPhone 17 Pro 256G", 40, "B/A", None),
        ("Apple iPhone 17 Pro Max ", 50, "B/A", None),
        ("Apple iPhone 13 Pro 256G", 50, "NON-EU", "CPO"),
        ("Apple iPhone 16 Pro Max ", 50, "NON-EU", "CPO"),
    ]
