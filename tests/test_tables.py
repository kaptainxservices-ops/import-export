"""Table extraction: header detection, column mapping, section headings, row building.

The grids here are taken from the real sample spreadsheets and HTML bodies, including
their preambles and separator rows.
"""

import pytest

from app.brain.tables import Grid, detect_header_row, map_columns, parse_grid
from app.brain.tables.reader import read_html_tables

# ---------------------------------------------------------------- header detection


def test_header_found_in_row_zero():
    grid = [["Brand", "Description", "EAN", "Ready", "Incoming", "Prezzo EUR"]]
    assert detect_header_row(grid) == 0


def test_header_found_past_a_prose_preamble():
    """SMTR opens with seven rows of greeting and trading terms. Assuming row 0 would
    read every product row against the wrong fields and produce plausible rubbish."""
    grid = [
        ["Dear Partner,", "", "", "", "", ""],
        ["", "", "", "", "", ""],
        ["Kindly check our WTS list below", "", "", "", "", ""],
        ["- All models are brand new", "", "", "", "", ""],
        ["- MOQ of 50 pcs", "", "", "", "", ""],
        ["- We add Insurance on request", "", "", "", "", ""],
        ["- For XML and API contact us", "", "", "", "", ""],
        ["Model", "EAN", "Qty", "Price", "", ""],
        ["iPhone 15 128GB Black", "0195949043017", "50", "705", "", ""],
    ]
    assert detect_header_row(grid) == 7


def test_header_found_after_a_blank_row():
    grid = [
        ["", "", "", "", ""],
        ["Brand", "Ean", "Description", "Qty", "Price"],
        ["AKAI", "'6901443360147", "SMART FITNESS WATCH", "1", "10"],
    ]
    assert detect_header_row(grid) == 1


def test_no_header_is_reported_rather_than_assumed():
    """This is the one case that justifies a model call — once for the table, not once
    per row."""
    grid = [["some text", "more text"], ["and", "more"]]
    assert detect_header_row(grid) is None


def test_a_single_matching_cell_is_not_a_header():
    """A row containing only the word 'Price' is a label, not a header."""
    assert detect_header_row([["Price"], ["905"]]) is None


# ---------------------------------------------------------------- column mapping


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (["Brand", "Description", "EAN", "Ready", "Incoming", "Prezzo EUR"],
         {"brand": 0, "description": 1, "ean": 2, "quantity": 3, "price": 5}),
        (["Artikelnummer", "Brand", "Artikelbezeichnung", "EAN-Nummer", "Partcode",
          "Lagerbestand / QTY", "Preis / Price €"],
         {"code": 0, "brand": 1, "description": 2, "ean": 3, "quantity": 5, "price": 6}),
        (["Code", "Brand", "Category", "Description", "Specifications", "Price\nEXW", "EAN"],
         {"code": 0, "brand": 1, "category": 2, "description": 3, "price": 5, "ean": 6}),
    ],
)
def test_real_headers_map_correctly(header, expected):
    mapped = map_columns(header).columns
    for field_name, index in expected.items():
        assert mapped.get(field_name) == index, f"{field_name} mapped to {mapped.get(field_name)}"


def test_headers_in_other_languages():
    """Suppliers write headers in German, Italian, Polish, Dutch and Romanian."""
    mapped = map_columns(["Marke", "Bezeichnung", "Menge", "Preis"]).columns
    assert mapped == {"brand": 0, "description": 1, "quantity": 2, "price": 3}


def test_currency_is_read_from_the_header():
    """'Preis / Price €' names the currency for every row beneath it, which removes the
    guesswork entirely."""
    assert map_columns(["Description", "Qty", "Preis / Price €"]).currency == "EUR"
    assert map_columns(["Description", "Prezzo EUR"]).currency == "EUR"


def test_leftmost_quantity_wins():
    """'Ready' and 'Incoming' are both quantities; stock on hand is the one that can
    be sold today."""
    assert map_columns(["Description", "Ready", "Incoming", "Price"]).columns["quantity"] == 1


def test_table_without_a_price_is_not_usable():
    assert map_columns(["Description", "Qty"]).is_usable is False
    assert map_columns(["Description", "Qty", "Price"]).is_usable is True


# ---------------------------------------------------------------- row building


def _grid(rows):
    return Grid(rows=rows, source="Sheet1")


def test_rows_are_extracted_with_provenance():
    table = parse_grid(_grid([
        ["Brand", "Description", "EAN", "Qty", "Price"],
        ["APPLE", "APPLE IPHONE 17 256GB WHITE", "0195950643701", "29", "749"],
    ]))
    assert table.row_count == 1

    row = table.rows[0]
    assert row.description == "APPLE IPHONE 17 256GB WHITE"
    assert row.ean == "0195950643701"
    assert row.quantity == 29
    assert row.price == 749.0
    assert "row 2" in row.source_ref


def test_section_headings_are_inherited():
    """Automic's list names 'Samsung' once and never again. Without inheritance every
    row beneath it has no brand at all."""
    table = parse_grid(_grid([
        ["Description", "Qty", "Price"],
        ["Samsung", "", ""],
        ["A17 5G DS SM-A176 4+128 Black", "100", "135"],
        ["A27 5G DS SM-A276B 6+128 Black", "50", "185"],
    ]))
    assert table.row_count == 2
    assert all(r.section == "Samsung" for r in table.rows)
    assert all(r.brand == "Samsung" for r in table.rows)


def test_a_later_heading_replaces_the_earlier_one():
    table = parse_grid(_grid([
        ["Description", "Qty", "Price"],
        ["APPLE", "", ""],
        ["IPHONE 17 256GB", "10", "749"],
        ["NINTENDO", "", ""],
        ["Switch 2 Console Black", "800", "378"],
    ]))
    assert [r.section for r in table.rows] == ["APPLE", "NINTENDO"]


def test_european_prices_use_the_supplier_hint():
    table = parse_grid(
        _grid([
            ["Artikelbezeichnung", "Lagerbestand / QTY", "Preis / Price €"],
            ["4smarts Pico Dual 20W Car Charger", "3", "2,50"],
        ]),
        decimal_hint="comma",
    )
    assert table.rows[0].price == 2.50
    assert table.rows[0].currency == "EUR"


@pytest.mark.parametrize(
    "row",
    [
        ["", "10", "705"],                                  # no description
        ["Total", "", "12500"],                             # a totals row
        ["Dear Partner,", "", ""],                          # prose
        ["iPhone 15 128GB Black", "", ""],                  # no price and no quantity
        ["12345", "10", "705"],                             # description is a stray number
    ],
)
def test_non_product_rows_are_skipped(row):
    """An empty offer on the board is worse than a missing one — it looks like stock."""
    table = parse_grid(_grid([["Description", "Qty", "Price"], row]))
    assert table.row_count == 0


def test_skipped_rows_are_counted_not_silently_dropped():
    table = parse_grid(_grid([
        ["Description", "Qty", "Price"],
        ["iPhone 15 128GB", "10", "705"],
        ["Total", "", "7050"],
    ]))
    assert table.row_count == 1
    assert table.skipped_rows == 1


def test_unparseable_price_is_flagged_on_the_row():
    table = parse_grid(_grid([
        ["Description", "Qty", "Price"],
        ["iPhone 15 128GB", "10", "on request"],
    ]))
    assert table.rows[0].price is None
    assert table.rows[0].warnings


def test_unrecognisable_table_asks_for_column_mapping():
    table = parse_grid(_grid([["a", "b"], ["c", "d"]]))
    assert table.needs_column_mapping is True
    assert table.row_count == 0


# ---------------------------------------------------------------- HTML reading


def test_html_tables_are_read():
    html = """<table>
      <tr><th>Description</th><th>Qty</th><th>Price</th></tr>
      <tr><td>iPhone 15 128GB Black</td><td>50</td><td>705</td></tr>
      <tr><td>iPhone 16 128GB Teal</td><td>20</td><td>805</td></tr>
      <tr><td>iPhone 17 256GB White</td><td>29</td><td>749</td></tr>
    </table>"""
    grids = read_html_tables(html)
    assert len(grids) == 1
    assert parse_grid(grids[0]).row_count == 3


def test_layout_tables_are_ignored():
    """Signature blocks and logo banners are tables too."""
    assert read_html_tables("<table><tr><td>logo</td></tr></table>") == []


def test_nested_layout_tables_do_not_double_count():
    """Newsletters nest tables for formatting. Importing both the outer and the inner
    one doubles a supplier's row count overnight and misfires the safety check."""
    html = """<table><tr><td>
        <table>
          <tr><th>Description</th><th>Qty</th><th>Price</th></tr>
          <tr><td>iPhone 15 128GB</td><td>50</td><td>705</td></tr>
          <tr><td>iPhone 16 128GB</td><td>20</td><td>805</td></tr>
          <tr><td>iPhone 17 256GB</td><td>29</td><td>749</td></tr>
        </table>
    </td></tr></table>"""
    grids = read_html_tables(html)
    assert len(grids) == 1


def test_leaf_tables_are_taken_not_wrappers():
    """Yukatel and Landotech nest tables several deep. Taking the outer one yields
    cells whose text is everything inside flattened together, so a row reads 'Dear
    Yogesh, I hope this message finds you well' in all six columns — and parses as a
    product with no error anywhere."""
    html = """<table><tr><td>Dear Yogesh, I hope this message finds you well</td></tr>
      <tr><td>
        <table>
          <tr><th>Description</th><th>Qty</th><th>Price</th></tr>
          <tr><td>iPhone 15 128GB Black</td><td>50</td><td>705</td></tr>
          <tr><td>iPhone 16 128GB Teal</td><td>20</td><td>805</td></tr>
          <tr><td>iPhone 17 256GB White</td><td>29</td><td>749</td></tr>
        </table>
      </td></tr>
      <tr><td>Best regards</td></tr><tr><td>Landotech</td></tr></table>"""
    grids = read_html_tables(html)
    assert len(grids) == 1
    rows = parse_grid(grids[0]).rows
    assert len(rows) == 3
    assert "Dear Yogesh" not in rows[0].description


# ---------------------------------------------------------------- header oddities


def test_price_column_headed_only_with_a_currency():
    """Masterfone heads its price column 'USD' and nothing else. Read literally that is
    a currency column with no price, and the whole 2,235-row table is unparseable."""
    table = parse_grid(_grid([
        ["Brand", "Models", "Colour", "Remarks", "USD"],
        ["Amazon", "Echo Spot 2024 speaker", "black", "", "49.5"],
    ]))
    assert table.row_count == 1
    assert table.rows[0].price == 49.5
    assert table.rows[0].currency == "USD"


def test_blank_header_over_the_product_column():
    """GOtel labels every column except the one naming the product."""
    table = parse_grid(_grid([
        ["", "EAN", "Price", "Quantity", "Purchased"],
        ["Apple iPhone 15 128GB Black", "195949035999", "579,00", "50", "0"],
        ["Apple USB-C Power Adapter", "195949121296", "11,90", "79", "0"],
    ]), decimal_hint="comma")
    assert table.row_count == 2
    assert table.rows[0].description.startswith("Apple iPhone 15")
    assert table.rows[0].price == 579.00


def test_currency_read_from_the_price_cell_when_the_header_is_silent():
    """GOtel labels the column plainly 'Price' and writes '11,90 €' in every cell.
    Without this, a 219-row list lands with no currency, and an offer with no currency
    cannot be compared against anything."""
    table = parse_grid(_grid([
        ["", "EAN", "Price", "Quantity"],
        ["Apple USB-C Power Adapter 20W White", "195949121296", "11,90 €", "79"],
        ["Apple iPhone 15 128GB Black", "195949035999", "579,00 €", "50"],
    ]))
    assert table.rows[0].currency == "EUR"
    assert table.rows[0].price == 11.90


def test_header_currency_beats_the_cell():
    table = parse_grid(_grid([
        ["Description", "Qty", "Preis / Price €"],
        ["Apple iPhone 15 128GB", "50", "579"],
    ]))
    assert table.rows[0].currency == "EUR"


def test_repeated_headers_inside_the_body_are_skipped():
    """GOtel reprints its header every few rows as a separator. Parsed as data those
    become offers named 'EAN' priced at 'Price'."""
    table = parse_grid(_grid([
        ["", "EAN", "Price", "Quantity"],
        ["Apple iPhone 15 128GB Black", "195949035999", "579", "50"],
        ["", "EAN", "Price", "Quantity"],
        ["Apple iPhone 16 128GB Teal", "195949036002", "679", "45"],
    ]))
    assert table.row_count == 2
    assert all("EAN" != r.description for r in table.rows)


def test_headerless_two_column_list_is_read():
    """AB Business sends bare description-and-price pairs under section headings."""
    table = parse_grid(_grid([
        ["Covid 19 Protection", ""],
        ["Kit Test Antigen Sejoy (09/2027)", "0,20"],
        ["Mask KN95 / FFP2", "0,05"],
        ["Hand Gel 100 ml (80% Alcol)", "0,30"],
        ["Hand Gel 500 ml (70% Alcol)", "0,70"],
    ]), decimal_hint="comma")
    assert table.row_count >= 4
    assert table.rows[0].price == 0.20


def test_wide_headerless_tables_are_not_guessed_at():
    """Guessing at a wide table with no header would file prices into the quantity
    column. A wrong price is worse than a missing row — it gets quoted to a buyer."""
    grid = _grid([[f"item {i}", "a", "b", "c", str(i), "x"] for i in range(8)])
    assert parse_grid(grid).needs_column_mapping is True


def test_reader_never_raises_on_broken_html():
    for junk in [None, "", "<table><tr><td>", "not html at all", "<<<>>>"]:
        read_html_tables(junk)
