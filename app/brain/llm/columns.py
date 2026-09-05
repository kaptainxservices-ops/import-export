"""Label the columns of a table whose header the rules could not read.

`columns.py` in the tables package recognises headers in seven languages and still meets
sheets it cannot place: a header written in Romanian abbreviations, a column called
'Cant.', a sheet with no header at all. Those tables are currently dropped whole — a
219-row list reduced to nothing because one cell was worded unexpectedly.

The model is asked to label columns, and that is all it is asked. It sees the header and
five sample rows, and returns indices. It never sees the other 2,230 rows and never
returns a value, so the worst it can do is point at the wrong column — and pointing at
the wrong column is checkable, which is what `_corroborate` does before anything is
believed.
"""

from __future__ import annotations

import json
import logging
import re

from app.brain.llm.client import Answer, ask, available
from app.brain.llm.usage import UsageLog
from app.brain.normalise import parse_price, parse_quantity
from app.brain.tables.columns import ColumnMap, is_wordy
from app.brain.tables.parser import ColumnMapper

log = logging.getLogger(__name__)

# The fields a column may be labelled as. Anything outside this set is discarded rather
# than passed through, so a new field name cannot appear on a row by way of a prompt.
_FIELDS = (
    "description", "price", "quantity", "ean", "brand",
    "code", "colour", "currency", "category",
)

_SAMPLE_ROWS = 5
_MAX_COLUMNS = 24

_SYSTEM = """You label the columns of a price-list table from the wholesale phone trade.

You are given a header row (sometimes blank or missing) and a few data rows. Every cell \
is prefixed with its column index.

Reply with a JSON object mapping field names to column indices, and nothing else:

  {"description": 1, "price": 4, "quantity": 3}

Field names you may use: description, price, quantity, ean, brand, code, colour, \
currency, category. Omit any field the table does not have. Never use an index that is \
not shown.

  description  the column naming the product, e.g. 'iPhone 13 128GB Black'
  price        the unit selling price. Not a total, not an RRP, not a discount
  quantity     units available. Where a sheet has both 'ready' and 'incoming', take ready
  ean          a barcode: 8, 12, 13 or 14 digits and nothing else

Headers may be in German, Italian, Polish, Dutch, Romanian, Spanish or English, or \
absent entirely — in which case judge by the contents.

If you cannot identify both a description column and a price column, reply {}. A table \
skipped is reviewed by a person; a table read against the wrong columns puts prices in \
the quantity field and reaches a customer as a quote."""


# A supplier sends the same header every morning for years. Keyed on the header text plus
# the table's width, so the first list of the day pays and the rest do not. Process-local:
# it resets when Render restarts, which is the right trade for now. The permanent home for
# a settled mapping is a column on the counterparty, so a human correction can outlive it.
_cache: dict[str, ColumnMap | None] = {}


def mapper_for(usage: UsageLog) -> ColumnMapper | None:
    """A mapper bound to one email's usage log, or `None` if there is no model.

    Returning `None` rather than a no-op function is deliberate: `parse_grid` then skips
    building the sample at all, and the code path with no key is the code path that
    existed before this module.
    """
    if not available():
        return None

    def mapper(header: list[str], sample: list[list[str]]) -> ColumnMap | None:
        return map_columns_llm(header, sample, usage=usage)

    return mapper


def map_columns_llm(
    header: list[str],
    sample: list[list[str]],
    usage: UsageLog | None = None,
) -> ColumnMap | None:
    """Ask the model what the columns of this table mean."""
    if not available() or not sample:
        return None

    width = min(max((len(row) for row in [header, *sample]), default=0), _MAX_COLUMNS)
    if width < 2:
        return None

    key = _signature(header, width)
    if key in _cache:
        return _cache[key]

    answer = ask(
        _SYSTEM,
        _render(header, sample, width),
        role="extract",
        max_tokens=200,
        prefill="{",
    )
    if usage is not None:
        usage.add("columns", answer)

    if answer is None:
        # Not cached. A rate limit now says nothing about this header, and caching the
        # failure would blind the supplier's table for the life of the process.
        return None

    mapping = _validate(answer.text, width)
    if mapping is not None:
        mapping = _corroborate(mapping, sample)

    if mapping is None:
        log.info("model could not map columns for header %r", header[:6])

    _cache[key] = mapping
    return mapping


def cost_of(answer: Answer | None) -> float:
    return answer.cost_usd if answer else 0.0


def clear_cache() -> None:
    """For tests, and for the admin panel when a mapping is corrected by hand."""
    _cache.clear()


# ---------------------------------------------------------------- prompt


def _signature(header: list[str], width: int) -> str:
    cells = "|".join((c or "").strip().lower() for c in header[:_MAX_COLUMNS])
    return f"{width}:{cells}"


def _render(header: list[str], sample: list[list[str]], width: int) -> str:
    lines = [_cells("header", header, width)]
    lines.extend(
        _cells(f"row {n}", row, width) for n, row in enumerate(sample[:_SAMPLE_ROWS], start=1)
    )
    return "\n".join(lines)


def _cells(label: str, row: list[str], width: int) -> str:
    # Long cells are cut: a description column sometimes carries a paragraph of terms,
    # and the model needs to see which column it is, not read it.
    parts = [f"[{i}] {(row[i] if i < len(row) else '')!r}"[:80] for i in range(width)]
    return f"{label}: " + " | ".join(parts)


# ---------------------------------------------------------------- verification


def _validate(text: str, width: int) -> ColumnMap | None:
    """Turn the reply into a ColumnMap, discarding everything that is not an index."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None

    columns: dict[str, int] = {}
    used: set[int] = set()

    for name, index in data.items():
        if name not in _FIELDS:
            continue
        # `isinstance(True, int)` is True in Python, so a reply of {"price": true} would
        # otherwise map the price to column 1.
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not 0 <= index < width:
            continue
        # Two fields cannot be the same column. The first stated wins and the rest are
        # dropped; if that costs us the description or the price, the mapping fails the
        # usability check below and the table goes to review.
        if index in used:
            continue
        columns[name] = index
        used.add(index)

    mapping = ColumnMap(columns=columns)
    return mapping if mapping.is_usable else None


def _corroborate(mapping: ColumnMap, sample: list[list[str]]) -> ColumnMap | None:
    """Check the model's claims against the cells it claimed them about.

    This is the step that makes the whole thing safe. The model proposes; the sample rows
    decide. A column called `price` whose cells do not parse as money is not a price
    column, whatever it was labelled, and reading a table against a wrong price column
    produces rows that look entirely plausible on the board.

    Description and price must hold up or the mapping is refused outright. The optional
    fields are dropped one by one instead: losing a quantity leaves a row that still says
    what is for sale and what it costs, while a wrong quantity promises stock nobody has.
    """
    checks = {
        "description": lambda v: is_wordy(v),
        "price": lambda v: parse_price(v) is not None,
        "quantity": lambda v: parse_quantity(v) is not None,
        "ean": lambda v: v.strip().isdigit() and len(v.strip()) in (8, 12, 13, 14),
    }

    survivors = dict(mapping.columns)

    for field_name, holds in checks.items():
        index = mapping.columns.get(field_name)
        if index is None:
            continue

        values = [row[index].strip() for row in sample if index < len(row) and row[index].strip()]
        if len(values) < 2:
            # Too little evidence either way. Required fields cannot be taken on trust;
            # optional ones are dropped rather than guessed at.
            if field_name in ("description", "price"):
                return None
            survivors.pop(field_name, None)
            continue

        agreement = sum(1 for value in values if holds(value)) / len(values)
        if agreement >= 0.6:
            continue

        if field_name in ("description", "price"):
            log.info(
                "rejecting model mapping: column %d called %s but only %.0f%% of it fits",
                index, field_name, agreement * 100,
            )
            return None

        survivors.pop(field_name, None)

    result = ColumnMap(columns=survivors, currency=mapping.currency)
    return result if result.is_usable else None
