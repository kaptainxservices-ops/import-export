"""Getting a grid of cells out of HTML bodies and spreadsheet files.

Reading only. No interpretation happens here — that is `columns` and `parser` — so
that a change to how a supplier's file is shaped never turns into a change to how
offers are understood.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# Below this a 'table' is layout: a logo, a signature block, a two-cell header banner.
MIN_TABLE_ROWS = 4


@dataclass
class Grid:
    """A rectangular block of text cells, and where it came from."""

    rows: list[list[str]] = field(default_factory=list)
    source: str = ""

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)


def read_html_tables(html: str | None, min_rows: int = MIN_TABLE_ROWS) -> list[Grid]:
    """Every table in an HTML body that is big enough to be a price list.

    Only leaf tables are taken — a table that itself contains a table big enough to be
    a price list is a layout wrapper, not data.

    This is the opposite of the obvious rule, and getting it the wrong way round is
    quietly destructive rather than loud. Yukatel and Landotech nest tables several
    deep for formatting; keeping the outermost one yields cells whose text is the
    flattened content of everything inside, so a row reads
    'Dear Yogesh, I hope this message finds you well' in all six columns. That parses
    as a product with no error anywhere.
    """
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    grids: list[Grid] = []
    for index, table in enumerate(soup.find_all("table")):
        if any(len(inner.find_all("tr")) >= min_rows for inner in table.find_all("table")):
            continue

        # A table nested in a cell is one of two things. In a row that has content of
        # its own it is formatting -- MACWAY's one-row table inside its header cell, a
        # price wrapped in a table of its own -- and its text belongs to that cell.
        # Counted as cells instead, it gave MACWAY's header five columns over three-
        # column data and the list read as nothing, every day. In a row that is nothing
        # but a wrapper, the nested table's rows ARE the content: Yukatel's few products
        # per tiny table. A nested table big enough to be a list of its own is never
        # flattened into a cell: it is read as its own grid.
        rows: list[list[str]] = []
        _collect(table, rows, min_rows)

        # Whether the table is big enough to be a list is judged as before, by every row
        # in it, nested ones included: Yukatel's three-product tables inside a one-row
        # wrapper are a list together, and only the content rule above changed.
        size = sum(
            1 for tr in table.find_all("tr")
            if any(td.get_text(strip=True) for td in tr.find_all(["td", "th"]))
        )
        if rows and size >= min_rows:
            grids.append(Grid(rows=rows, source=f"table {index + 1}"))

    return grids


def _collect(table, out: list[list[str]], min_rows: int) -> None:
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        cells = tr.find_all(["td", "th"], recursive=False)
        nested = [t for t in tr.find_all("table") if t.find_parent("table") is table]
        if nested and not any(_own_text(td, table) for td in cells):
            for inner in nested:
                if len(inner.find_all("tr")) < min_rows:
                    _collect(inner, out, min_rows)
            continue
        texts = [_clean(_text_without_big_tables(td, min_rows)) for td in cells]
        if any(texts):
            out.append(texts)


def _text_without_big_tables(cell, min_rows: int) -> str:
    """A cell's text including small nested tables (formatting), excluding any nested
    table big enough to be a list of its own."""
    big = [t for t in cell.find_all("table") if len(t.find_all("tr")) >= min_rows]
    return " ".join(
        piece.strip() for piece in cell.find_all(string=True)
        if piece.strip() and not any(b in piece.parents for b in big)
    )


def _own_text(cell, owner) -> str:
    """A cell's text without the text of any table nested inside it."""
    return " ".join(
        piece.strip() for piece in cell.find_all(string=True)
        if piece.strip() and piece.find_parent("table") is owner
    )


def read_spreadsheet(data: bytes, filename: str = "") -> list[Grid]:
    """Read .xlsx / .xlsm or legacy .xls into one grid per sheet.

    Legacy .xls needs a different library entirely, and one supplier in the sample
    (Parktel) still sends it. Failing to read a file is reported as an empty result
    rather than an exception — one unreadable attachment must not abandon the email.
    """
    name = filename.lower()
    if name.endswith(".xls"):
        return _read_xls(data, filename)
    return _read_xlsx(data, filename)


def _read_xlsx(data: bytes, filename: str) -> list[Grid]:
    try:
        import openpyxl
    except ImportError:  # pragma: no cover
        return []

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        return []

    grids: list[Grid] = []
    try:
        for sheet_name in book.sheetnames:
            sheet = book[sheet_name]
            rows = [
                [_clean(_stringify(cell)) for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            rows = [r for r in rows if any(r)]
            if rows:
                grids.append(Grid(rows=rows, source=f"{filename or 'workbook'}!{sheet_name}"))
    finally:
        book.close()

    return grids


def _read_xls(data: bytes, filename: str) -> list[Grid]:
    try:
        import xlrd
    except ImportError:  # pragma: no cover
        return []

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception:
        return []

    grids: list[Grid] = []
    for sheet in book.sheets():
        rows = []
        for index in range(sheet.nrows):
            cells = [_clean(_stringify(v)) for v in sheet.row_values(index)]
            if any(cells):
                rows.append(cells)
        if rows:
            grids.append(Grid(rows=rows, source=f"{filename or 'workbook'}!{sheet.name}"))

    return grids


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        # Excel stores every number as a float, so a quantity of 50 arrives as 50.0 and
        # an EAN as 4.25201190776e+12. Integral values are rendered as integers so that
        # '50' does not become '50.0' and fail to parse as a count.
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


_WHITESPACE = re.compile(r"[\s ​]+")


def _clean(text: str) -> str:
    """Collapse whitespace, including the non-breaking spaces Excel exports leave."""
    if not text:
        return ""
    return _WHITESPACE.sub(" ", str(text)).strip()
