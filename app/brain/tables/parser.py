"""Grid + column map -> line items.

Two behaviours here are worth stating, because both were driven by the real files.

**Section headings are inherited.** Automic's list groups everything under a 'Samsung'
heading and names no brand on any individual row: 'A17 5G DS SM-A176 4+128 — Black —
€135'. SELTE and All in Srl do the same with brand separator rows. Carrying the heading
down the rows beneath it recovers the brand for hundreds of lines that otherwise have
none — the general fix that the part-code pattern in `brands.py` only patches.

**A row is only accepted if it looks like a product.** Price lists are full of blank
rows, totals, notes, disclaimers and repeated headers. A row with no description, or
with no price and no quantity, is skipped rather than stored as an offer with empty
fields, because an empty offer on the board is worse than a missing one: it looks like
stock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.brain.normalise import (
    detect_brand,
    detect_category,
    normalise_currency,
    parse_price,
    parse_quantity,
)
from app.brain.normalise.product import normalise_gtin
from app.brain.tables.columns import (
    ColumnMap,
    detect_header_row,
    infer_description_column,
    is_wordy,
    looks_like_header,
    map_columns,
)
from app.brain.tables.reader import Grid

_EAN = re.compile(r"^\D{0,2}(\d{8}|\d{12,14})\D{0,2}$")
_NUMERIC_ONLY = re.compile(r"^[\d\s.,'-]+$")

# Rows that are commentary rather than stock.
_NON_PRODUCT = re.compile(
    r"^\s*(?:total|totale|gesamt|subtotal|sum|note|notes|remarks|terms|conditions|"
    r"dear\s|kindly|please|thank|regards|www\.|http)",
    re.IGNORECASE,
)


@dataclass
class TableRow:
    """One extracted line, before normalisation into an offer."""

    description: str
    ean: str | None = None
    colour: str | None = None
    quantity: int | None = None
    price: float | None = None
    currency: str | None = None
    brand: str | None = None
    category: str | None = None
    code: str | None = None
    section: str | None = None
    source_ref: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedTable:
    rows: list[TableRow] = field(default_factory=list)
    column_map: ColumnMap | None = None
    source: str = ""
    skipped_rows: int = 0
    needs_column_mapping: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)


def parse_grid(
    grid: Grid,
    decimal_hint: str | None = None,
    default_currency: str | None = None,
) -> ParsedTable:
    """Extract line items from one grid.

    `needs_column_mapping` is set when the header cannot be recognised. That is the
    only case that warrants a model call, and it is one call for the table — not one
    per row.
    """
    if not grid.rows:
        return ParsedTable(source=grid.source)

    header_index = detect_header_row(grid.rows)

    if header_index is None:
        # Plenty of lists have no header at all — AB Business sends two bare columns,
        # a description and a price, under a section heading. The shape is still
        # readable from the contents.
        inferred = _infer_headerless(grid.rows)
        if inferred is None:
            return ParsedTable(source=grid.source, needs_column_mapping=True)
        column_map, header_index = inferred, -1
    else:
        column_map = map_columns(grid.rows[header_index])
        column_map.header_row = header_index

        # A blank header cell over the product column is common: GOtel labels every
        # column except the one naming the product.
        if "description" not in column_map.columns:
            index = infer_description_column(
                grid.rows, header_index, set(column_map.columns.values())
            )
            if index is not None:
                column_map.columns["description"] = index

    if not column_map.is_usable:
        return ParsedTable(source=grid.source, column_map=column_map, needs_column_mapping=True)

    currency = column_map.currency or default_currency

    rows: list[TableRow] = []
    section: str | None = None
    skipped = 0

    for offset, raw in enumerate(grid.rows[header_index + 1 :], start=header_index + 2):
        heading = _as_section_heading(raw)
        if heading is not None:
            section = heading
            continue

        # GOtel reprints its header every few rows as a visual separator. Parsed as
        # data those become offers named 'EAN' priced at 'Price'.
        if looks_like_header(raw, minimum=3):
            skipped += 1
            continue

        row = _build_row(raw, column_map, currency, decimal_hint, section, grid.source, offset)
        if row is None:
            skipped += 1
            continue
        rows.append(row)

    return ParsedTable(rows=rows, column_map=column_map, source=grid.source, skipped_rows=skipped)


def _build_row(
    raw: list[str],
    column_map: ColumnMap,
    currency: str | None,
    decimal_hint: str | None,
    section: str | None,
    source: str,
    line_number: int,
) -> TableRow | None:
    def cell(name: str) -> str:
        index = column_map.get(name)
        if index is None or index >= len(raw):
            return ""
        return raw[index].strip()

    description = cell("description")
    if not description or _NON_PRODUCT.match(description):
        return None

    # A description that is only digits is a stray number, not a product.
    if _NUMERIC_ONLY.match(description):
        return None

    price = parse_price(cell("price"), decimal_hint=decimal_hint) if cell("price") else None
    quantity = parse_quantity(cell("quantity")) if cell("quantity") else None

    # No price and no quantity means the row carries no commercial information at all.
    # Storing it would put a phantom offer on the board.
    if price is None and quantity is None:
        return None

    # Brand order: the column, then the description, then the section heading the row
    # sits under. Automic names 'Samsung' once at the top and never again.
    brand = cell("brand") or detect_brand(description)
    if not brand and section:
        brand = detect_brand(section)

    row_currency = currency
    if cell("currency"):
        row_currency = normalise_currency(cell("currency"), default=currency)

    warnings: list[str] = []
    if cell("price") and price is None:
        warnings.append(f"unparseable price {cell('price')!r}")

    return TableRow(
        description=description,
        ean=_clean_ean(cell("ean")),
        # Sheets that give colour its own column do not repeat it in the description,
        # so ignoring it collapses every finish of a product into one identity.
        colour=cell("colour") or None,
        quantity=quantity,
        price=price,
        currency=row_currency,
        brand=brand.strip() if isinstance(brand, str) and brand.strip() else None,
        category=cell("category") or detect_category(description),
        code=cell("code") or None,
        section=section,
        source_ref=f"{source} row {line_number}",
        warnings=warnings,
    )


def _infer_headerless(rows: list[list[str]], sample: int = 40) -> ColumnMap | None:
    """Read a narrow table that has no header at all.

    Only attempted on two or three columns, and only when one is clearly text and
    another clearly numeric. Guessing at a wide table with no header would silently
    file prices into the quantity column, and a wrong price is worse than a missing
    row — a wrong price gets quoted to a buyer.
    """
    body = [r for r in rows[:sample] if any(c.strip() for c in r)]
    if len(body) < 4:
        return None

    width = max(len(r) for r in body)
    if width < 2 or width > 3:
        return None

    description_index: int | None = None
    price_index: int | None = None

    for index in range(width):
        values = [r[index].strip() for r in body if index < len(r) and r[index].strip()]
        if len(values) < 3:
            continue

        priceable = sum(1 for v in values if parse_price(v) is not None and len(v) < 16)
        wordy = sum(1 for v in values if is_wordy(v))

        if wordy / len(values) >= 0.7 and description_index is None:
            description_index = index
        elif priceable / len(values) >= 0.7 and price_index is None:
            price_index = index

    if description_index is None or price_index is None:
        return None

    return ColumnMap(columns={"description": description_index, "price": price_index})


def _as_section_heading(raw: list[str]) -> str | None:
    """A row carrying one short label and nothing else is a section heading.

    SELTE writes brand separators as ['', '', 'AKAI', '', '']; All in Srl writes
    ['APPLE', '', '', '', '', '']. Both mean 'everything below this is that brand'.
    """
    filled = [c.strip() for c in raw if c and c.strip()]
    if len(filled) != 1:
        return None

    label = filled[0]
    if len(label) > 40 or _NON_PRODUCT.match(label):
        return None
    # A lone number is a stray value, not a heading.
    if _NUMERIC_ONLY.match(label):
        return None

    return label


def _clean_ean(value: str) -> str | None:
    """Only accept a cell that is *only* a barcode.

    A description column that happens to contain a 13-digit number is not an EAN
    column, and since EAN is the identity key, a wrong one merges two unrelated
    products into a single offer.
    """
    if not value:
        return None
    if not _EAN.match(value.strip()):
        return None
    return normalise_gtin(value)
