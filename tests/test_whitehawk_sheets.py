"""The shapes of Whitehawk's 26 October 2026 stock email, and what each one broke.

Built as small grids rather than read from the real files, so they run anywhere; the
real files are checked line by line in test_golden_whitehawk.py when they are present.

    Samsung grading sheet   212 lines -> 100 on the board. Grade column unread,
                            '128G' capacities unread, so lines collided.
    Colour-wise sheet       'Total_qty' unread (no quantities), 'Price | Euro | USD$'
                            read as dollars when the prices are sterling.
    Model-wise sheet        the same stock again, totalled -- every product twice.
    Incoming sheet          'Total_qty' unread, so the 30 lines with no price had
                            neither price nor quantity and were thrown away.
"""

from app.brain.reconcile import IncomingOffer, _dedupe
from app.brain.summary import drop_summary_rows
from app.brain.tables.parser import parse_grid
from app.brain.tables.reader import Grid
from app.pipeline import _spec

GRADING_HEADER = ["Grade", "MFR", "Model", "Capacity", "Colour", "QTY", " Price GBP ",
                  "Euro", "USD $", "Tax Scheme"]


def grading(*rows):
    return Grid(rows=[["", "", "SAMSUNG MARGINAL VAT STOCK"], GRADING_HEADER, *rows],
                source="WH_SAMSUNG_GRADING.xlsx!Sheet1")


def line(grade, model, capacity, colour, qty, price):
    return [grade, "Samsung", model, capacity, colour, str(qty), str(price),
            str(round(price * 1.19, 2)), str(round(price * 1.34, 2)), "Margin"]


def offers(rows):
    out = []
    for row in rows:
        spec = _spec(row)
        out.append(IncomingOffer(
            identity_key=spec.identity_key(), quantity=row.quantity, unit_price=row.price,
            currency=row.currency, description=row.description, source_ref=row.source_ref,
            group=f"{row.source_ref.rsplit(' row ', 1)[0]}|{row.section or ''}",
        ))
    return out


# ---------------------------------------------------------------- the grading sheet


def test_every_grade_of_one_model_is_its_own_lot():
    table = parse_grid(grading(
        line("AA", "SAM S24FE", "128G", "Graphite", 14, 295),
        line("B", "SAM S24FE", "128G", "Graphite", 28, 255),
        line("C", "SAM S24FE", "128G", "Graphite", 17, 210),
    ))
    kept, dropped, _ = _dedupe(offers(table.rows))

    assert [r.grade for r in table.rows] == ["AA", "B", "C"]
    assert len(kept) == 3 and dropped == 0
    assert sorted(o.quantity for o in kept) == [14, 17, 28]


def test_short_capacities_are_read():
    table = parse_grid(grading(
        line("A", "SAM S24", "128G", "Black", 6, 310),
        line("A", "SAM S24", "256G", "Black", 3, 305),
        line("B", "SAM S25 ULTRA", "1T", "Grey", 1, 630),
        line("C", "SAM S9", "64G", "Black", 1, 60),
        line("C", "SAM FOLD 7", "1TB", "Black", 6, 610),
    ))
    assert [_spec(r).capacity_gb for r in table.rows] == [128, 256, 1024, 64, 1024]


def test_the_maker_column_names_the_brand():
    table = parse_grid(grading(line("A", "SAM S24", "128G", "Black", 6, 310)))
    spec = _spec(table.rows[0])
    assert spec.brand == "Samsung"
    assert spec.category == "phone"
    assert spec.model_key == "samsung:s24"


def test_two_batches_of_one_lot_on_one_sheet_are_added_together():
    """'A | SAM S25 ULTRA | 512G | Black | 3 | 615' and the same with 4 a line later."""
    table = parse_grid(grading(
        line("A", "SAM S25 ULTRA", "512G", "Black", 3, 615),
        line("A", "SAM S25 ULTRA", "512G", "Blue", 1, 615),
        line("A", "SAM S25 ULTRA", "512G", "Black", 4, 615),
    ))
    kept, dropped, combined = _dedupe(offers(table.rows))
    black = [o for o in kept if "black" in o.identity_key]
    assert combined == 1 and dropped == 0
    assert black[0].quantity == 7


def test_the_same_line_at_a_different_price_is_still_kept_once():
    """Two prices for one identity is not two batches -- it is a list contradicting
    itself, and the first line stands as before."""
    table = parse_grid(grading(
        line("A", "SAM S24", "128G", "Black", 3, 310),
        line("A", "SAM S24", "128G", "Black", 4, 299),
    ))
    kept, dropped, combined = _dedupe(offers(table.rows))
    assert len(kept) == 1 and dropped == 1 and combined == 0
    assert kept[0].quantity == 3


def test_the_price_currency_comes_from_the_price_column():
    table = parse_grid(grading(line("A", "SAM S24", "128G", "Black", 6, 310)))
    assert table.rows[0].currency == "GBP"
    assert table.rows[0].price == 310


# ---------------------------------------------------------------- the colour-wise sheet

COLOUR_HEADER = ["MAKE", "MODEL", "Colours", "Total_qty", "Price", "Euro", "USD$",
                 "EAN NUMBER", "SPEC"]


def colour_wise(*rows, header=COLOUR_HEADER, source="WH_BRAND_NEW.xlsx!COLOUR-WISE"):
    return Grid(rows=[["", "", "BRAND NEW HANDSETS"], header, *rows], source=source)


def priced(make, model, colour, qty, gbp, ean, spec="EU-UK"):
    return [make, model, colour, str(qty), str(gbp), str(round(gbp * 1.19, 2)),
            str(round(gbp * 1.35, 2)), ean, spec]


def test_an_underscored_quantity_header_is_read():
    table = parse_grid(colour_wise(
        priced("APPLE", "APPLE IPHONE 18 PRO 5G 256GB (A3714) NEW", "GLACIER", 7, 985,
               "195951416496"),
    ))
    assert table.rows[0].quantity == 7


def test_a_bare_price_beside_euro_and_dollar_columns_is_sterling():
    """985 beside 1172.15 EUR and 1329.75 USD can only be pounds."""
    table = parse_grid(colour_wise(
        priced("APPLE", "APPLE IPHONE 18 PRO 5G 256GB (A3714) NEW", "GLACIER", 7, 985,
               "195951416496"),
        priced("HONOR", "HONOR 600 PRO 5G 512GB 12GB (VKP-NX9) NEW", "GOLDEN WHITE", 123,
               468, "6936520895120"),
        priced("MOTOROLA", "MOTOROLA MOTO G06 5G 64GB 4GB (XT2535-3) NEW", "BLUE", 449, 78,
               "840493601762"),
    ))
    assert {r.currency for r in table.rows} == {"GBP"}
    assert [r.price for r in table.rows] == [985, 468, 78]


def test_a_bare_price_equal_to_a_labelled_column_takes_its_currency():
    header = ["MODEL", "Qty", "Price", "EUR"]
    rows = [["Galaxy S24 128GB Black", "5", "500", "500"],
            ["Galaxy S24 256GB Black", "5", "550", "550"]]
    table = parse_grid(Grid(rows=[header, *rows], source="x"))
    assert {r.currency for r in table.rows} == {"EUR"}


def test_when_the_ratios_prove_nothing_the_labelled_price_is_used():
    """A bare column whose siblings are not conversions of it is not guessed at: the
    price the supplier labelled is used instead."""
    header = ["MODEL", "Qty", "Price", "USD"]
    rows = [["Galaxy S24 128GB Black", "5", "500", "610"],
            ["Galaxy S24 256GB Black", "5", "550", "620"],
            ["Galaxy S24 512GB Black", "5", "580", "700"]]
    table = parse_grid(Grid(rows=[header, *rows], source="x"))
    assert [(r.price, r.currency) for r in table.rows] == [(610, "USD"), (620, "USD"),
                                                         (700, "USD")]


def test_the_spec_column_is_the_market():
    table = parse_grid(colour_wise(
        priced("ONEPLUS", "ONEPLUS 15R 256GB 12GB (CPH2767)", "CHARCOAL BLACK", 1, 340,
               "6921815631088", spec="INDIA"),
        priced("HONOR", "HONOR SUPERCHARGER POWER ADAPTER GaN SLIM 100W", "WHITE", 595, 14,
               "6936520853021", spec="UK"),
        priced("HONOR", "HONOR MAGICPAD4 WiFi 512GB 16GB (YLE-W09) NEW", "GRAY", 39, 430,
               "6936520891900", spec="EU-UK"),
    ))
    assert [_spec(r).region_code for r in table.rows] == ["INDIA", "UK", "EU"]


def test_a_misspelt_maker_is_still_the_maker():
    table = parse_grid(colour_wise(
        priced("XIOAMI", "PAD 7 / 7 PRO COVER (2409CTC1DC) NEW", "BLACK", 4, 5,
               "6941812722206"),
    ))
    assert _spec(table.rows[0]).brand == "Xiaomi"


def test_a_galaxy_watch_is_not_an_apple_watch():
    table = parse_grid(colour_wise(
        priced("SAMSUNG", "GALAXY WATCH ULTRA (SM-L705F) NEW", "TITANIUM WHITE", 1, 205,
               "8806095687889", spec="INDIA"),
    ))
    spec = _spec(table.rows[0])
    assert spec.brand == "Samsung"
    assert spec.model_key == "samsung:watchultra"


# ---------------------------------------------------------------- the summary sheet

MODEL_HEADER = ["TOTAL QTY", "MODEL", "PRICE IN GBP", "PRICE IN EURO", "PRICE IN USD $", "SPEC"]


def test_a_sheet_that_totals_another_sheet_is_left_out():
    detail = parse_grid(colour_wise(
        priced("HONOR", "HONOR MAGIC PAD 4 WIFI 256GB 12GB (YLE-W09) NEW", "WHITE", 2, 370,
               "6936520891917"),
        priced("HONOR", "HONOR MAGIC PAD 4 WIFI 256GB 12GB (YLE-W09) NEW", "GRAY", 10, 370,
               "6936520891924"),
        priced("HONOR", "PAD X9A-WIFI 128GB 6GB (ELN2-W29) NEW", "GREY", 7, 100,
               "6936520871964"),
        priced("HONOR", "HONOR CHOICE AI NOTE (ELC-ME00) NEW", "GREY", 144, 33,
               "6973014173227"),
    ))
    summary = parse_grid(Grid(rows=[["BRAND NEW TABLET"], MODEL_HEADER,
        ["HONOR"],
        ["12", "HONOR MAGIC PAD 4 WIFI 256GB 12GB (YLE-W09) NEW", "370", "440.3", "499.5", "EU-UK"],
        ["7", "PAD X9A-WIFI 128GB 6GB (ELN2-W29) NEW", "100", "119", "135", "EU-UK"],
        ["144", "HONOR CHOICE AI NOTE (ELC-ME00) NEW", "33", "39.27", "44.55", "EU-UK"],
        ["5", "HONOR PAD 20 WIFI 256GB 8GB (HEY4-W09) NEW", "320", "380.8", "432", "EU-UK"],
    ], source="WH_BRAND_NEW.xlsx!MODEL-WISE"))

    kept, dropped = drop_summary_rows(detail.rows + summary.rows)

    assert dropped == 3
    # The one line the detail sheet does not carry stays.
    assert [r.description for r in kept if "MODEL-WISE" in r.source_ref] == [
        "HONOR PAD 20 WIFI 256GB 8GB (HEY4-W09) NEW"
    ]


def test_two_lists_that_merely_overlap_are_both_kept():
    """A second sheet is only a summary when its quantities add up to the detail."""
    detail = parse_grid(colour_wise(
        priced("HONOR", "HONOR CHOICE AI NOTE (ELC-ME00) NEW", "GREY", 144, 33, "1"),
        priced("HONOR", "PAD X9A-WIFI 128GB 6GB (ELN2-W29) NEW", "GREY", 7, 100, "2"),
        priced("HONOR", "HONOR PAD 20 WIFI 256GB 8GB (HEY4-W09) NEW", "GRAY", 2, 320, "3"),
    ))
    other = parse_grid(Grid(rows=[MODEL_HEADER,
        ["50", "HONOR CHOICE AI NOTE (ELC-ME00) NEW", "33", "", "", ""],
        ["9", "PAD X9A-WIFI 128GB 6GB (ELN2-W29) NEW", "100", "", "", ""],
        ["1", "HONOR PAD 20 WIFI 256GB 8GB (HEY4-W09) NEW", "320", "", "", ""],
    ], source="OTHER.xlsx!Sheet1"))
    kept, dropped = drop_summary_rows(detail.rows + other.rows)
    assert dropped == 0 and len(kept) == 6


# ---------------------------------------------------------------- the incoming sheet

INCOMING_HEADER = ["MAKE", "MODEL", "Colours", "Total_qty", "PRICE IN GBP", "PRICE IN EURO",
                   "PRICE IN USD $", "EAN NUMBER"]


def incoming(*rows):
    return Grid(rows=[["", "INCOMING BRAND NEW STOCK (ETA 7-15 DAYS)"], INCOMING_HEADER, *rows],
                source="INCOMING_BRAND_NEW__STOCK_SHEET_26.10.26.xlsx!STOCK SHEET ")


def test_incoming_stock_is_marked_with_its_eta():
    table = parse_grid(incoming(
        ["JBL", "JBL WAVE BUDS 2 (JBLWBUDS2BLK) NEW", "BLACK", "108", "20", "23.5", "27",
         "1200130015373"],
    ))
    row = table.rows[0]
    assert (row.availability, row.availability_note) == ("incoming", "ETA 7-15 DAYS")
    assert row.quantity == 108


def test_incoming_lines_without_a_price_are_kept_with_their_quantity():
    table = parse_grid(incoming(
        ["JBL", "JBL WAVE BEAM 2 EARBUDS (JBLWBEAM2BLK) NEW", "BLACK", "1114", "", "", "",
         "1200130015250"],
        ["SONY", "SONY HEADPHONES WH-CH520 (YY2958) NEW", "BLUE", "180", "", "", "",
         "4548736142886"],
    ))
    assert [(r.quantity, r.price) for r in table.rows] == [(1114, None), (180, None)]


def test_incoming_and_warehouse_lots_of_one_barcode_stay_apart():
    """423 JBL Flip Essential 2 arriving and 50 in the warehouse, one EAN."""
    arriving = parse_grid(incoming(
        ["JBL", "JBL FLIP ESSENTIAL 2 SPEAKER (JBLFLIPES2) NEW", "GUN METAL", "423", "50",
         "58.75", "67.5", "6925281928840"],
    ))
    on_hand = parse_grid(colour_wise(
        priced("JBL", "JBL FLIP ESSENTIAL 2 SPEAKER (JBLFLIPES2) NEW", "GUN METAL", 50, 50,
               "6925281928840"),
    ))
    kept, dropped, _ = _dedupe(offers(arriving.rows + on_hand.rows))
    assert dropped == 0
    assert sorted(o.quantity for o in kept) == [50, 423]


def test_a_section_heading_can_say_the_rows_below_are_incoming():
    grid = Grid(rows=[["Model", "Qty", "Price EUR"],
                      ["Galaxy S24 128GB Black", "5", "500"],
                      ["Incoming next week"],
                      ["Galaxy S24 256GB Black", "8", "550"]], source="list.xlsx!Sheet1")
    rows = parse_grid(grid).rows
    assert [r.availability for r in rows] == [None, "incoming"]


def test_a_keyboard_for_a_tablet_is_an_accessory():
    table = parse_grid(colour_wise(
        priced("HONOR", "HONOR PAD X9B MAX | PAD X9 MAX SMART BLUETOOTH KEYBOARD "
               "(YAOMING-KEYBOARD) NEW", "DARK GRAY", 49, 30, "6942785107724"),
        priced("HONOR", "HONOR MAGIC-PENCIL 4S (AD57) NEW", "WHITE", 82, 35, "6936520890965"),
        priced("HONOR", "HONOR MAGICPAD4 WiFi 512GB 16GB (YLE-W09) NEW", "GRAY", 39, 430,
               "6936520891900"),
    ))
    assert [_spec(r).category for r in table.rows] == ["accessory", "accessory", "tablet"]
