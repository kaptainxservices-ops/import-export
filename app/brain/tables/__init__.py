"""Turning tables into line items, without a language model.

Six of the 45 sample emails carry 473,000 of the 652,000 characters in the corpus, and
one of them contains a 2,235-row table. Handing those to a model as raw text and asking
it to emit every row as JSON is slow, costs more in output tokens than input, and — the
part that actually matters — lets it invent a quantity. Code cannot invent a quantity.

So structured content is parsed deterministically:

    reader.py    HTML bodies, .xlsx and legacy .xls  ->  grids of cells
    columns.py   find the header row, map its columns to fields
    parser.py    grid + column map                   ->  line items

A model is only needed when the header cannot be recognised at all, and then only once
per table to map the columns — not once per row. That is the difference between a few
hundred tokens per email and tens of thousands.
"""

from app.brain.tables.columns import ColumnMap, detect_header_row, map_columns
from app.brain.tables.parser import ColumnMapper, TableRow, parse_grid
from app.brain.tables.reader import Grid, read_html_tables, read_spreadsheet

__all__ = [
    "ColumnMap",
    "ColumnMapper",
    "Grid",
    "TableRow",
    "detect_header_row",
    "map_columns",
    "parse_grid",
    "read_html_tables",
    "read_spreadsheet",
]
