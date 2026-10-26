"""Every line of a real email, checked against what the board would show.

Whitehawk's stock email of 26 October 2026: three workbooks, four sheets, 341 lines.
Before the fixes 196 lots reached the board and most of those carried a wrong field.
This reads each sheet independently -- straight out of openpyxl, with none of the
pipeline's own parsing -- and requires that every line arrives as a lot with the
right quantity, price, currency, capacity, colour, grade, market and availability.

The workbooks are client data and are not committed (samples/ is gitignored), so the
test skips where they are absent. Run it locally after any change to extraction:

    pytest tests/test_golden_whitehawk.py -v
"""

from __future__ import annotations

import base64
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.brain.reconcile import IncomingOffer, _dedupe
from app.brain.summary import drop_summary_rows
from app.pipeline import _spec, extract_rows, materialise_attachments
from app.schemas.email import Attachment, InboundEmail

FOLDER = Path(__file__).resolve().parents[1] / "samples" / "whitehawk-2026-10-26"
GRADING = FOLDER / "WH_SAMSUNG_GRADING_STOCK_26.10.26.xlsx"
BRAND_NEW = FOLDER / "WH_BRAND_NEW_STOCK_26.10.26.xlsx"
INCOMING = FOLDER / "INCOMING_BRAND_NEW__STOCK_SHEET_26.10.26.xlsx"

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in (GRADING, BRAND_NEW, INCOMING)),
    reason="Whitehawk sample workbooks not present (client data, not committed)",
)


def _board():
    """What the pipeline would put on the board for this email, lot by lot."""
    payload = InboundEmail(
        tenant_id="t", message_id="<golden>", from_email="pooja@whitehawkltd.com",
        received_at=datetime.now(timezone.utc), body_text="",  # noqa: UP017
        attachments=materialise_attachments([
            Attachment(filename=p.name, content_base64=base64.b64encode(p.read_bytes()).decode())
            for p in (BRAND_NEW, INCOMING, GRADING)
        ]),
    )
    rows, _, unmapped = extract_rows(payload, None, None, None)
    assert unmapped == 0
    rows, summarised = drop_summary_rows(rows)
    by_ref = {row.source_ref: row for row in rows}
    offers = [
        IncomingOffer(
            identity_key=_spec(row).identity_key(), quantity=row.quantity,
            unit_price=row.price, currency=row.currency, description=row.description,
            source_ref=row.source_ref,
            group=f"{row.source_ref.rsplit(' row ', 1)[0]}|{row.section or ''}",
        )
        for row in rows
    ]
    kept, dropped, combined = _dedupe(offers)
    return [(offer, by_ref[offer.source_ref], _spec(by_ref[offer.source_ref])) for offer in kept], \
        summarised, dropped, combined


def _sheet(path: Path, index: int = 0):
    import openpyxl

    return list(openpyxl.load_workbook(path, data_only=True).worksheets[index]
                .iter_rows(values_only=True))


def _gb(cell: str) -> int:
    match = re.fullmatch(r"(\d+)\s*(G|GB|T|TB)", str(cell).strip(), re.IGNORECASE)
    assert match, cell
    return int(match.group(1)) * (1024 if match.group(2).upper().startswith("T") else 1)


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower().replace("grey", "gray")


@pytest.fixture(scope="module")
def board():
    return _board()


def test_nothing_is_lost(board):
    lots, summarised, dropped, combined = board
    grading = [r for r in _sheet(GRADING)[2:] if r[2]]
    colour_wise = [r for r in _sheet(BRAND_NEW, 0)[2:] if r[0]]
    model_wise = [r for r in _sheet(BRAND_NEW, 1)[2:] if isinstance(r[0], int)]
    incoming = [r for r in _sheet(INCOMING)[2:] if r[1]]

    assert (len(grading), len(colour_wise), len(model_wise), len(incoming)) == (212, 42, 41, 46)
    # The model-wise sheet totals the colour-wise one and is left out whole.
    assert summarised == len(model_wise)
    # Two pairs of repeated Samsung lines are added together; nothing is dropped.
    assert (dropped, combined) == (0, 2)
    assert len(lots) == 212 - 2 + 42 + 46


def test_every_graded_samsung_line_is_on_the_board(board):
    lots, *_ = board
    found: dict[tuple, list] = defaultdict(list)
    for offer, row, spec in lots:
        if "GRADING" in row.source_ref:
            found[(row.description, spec.capacity_gb, _norm(spec.colour), spec.grade)].append(
                (offer.quantity, offer.unit_price, offer.currency)
            )

    expected: dict[tuple, list] = defaultdict(list)
    for grade, _, model, capacity, colour, qty, price, *_ in _sheet(GRADING)[2:]:
        if not model:
            continue
        key = (model, _gb(capacity), _norm(colour), grade)
        if expected[key] and expected[key][0][1] == price:
            expected[key][0] = (expected[key][0][0] + qty, price, "GBP")
        else:
            expected[key].append((qty, price, "GBP"))

    missing = {k: v for k, v in expected.items() if found.get(k) != v}
    assert not missing, f"{len(missing)} graded lines wrong or missing: {list(missing)[:5]}"
    assert sum(len(v) for v in found.values()) == 210


def test_every_colour_wise_line_has_its_quantity_price_and_market(board):
    lots, *_ = board
    found = {
        (row.description.strip(), _norm(row.colour)): (offer.quantity, offer.unit_price,
                                                        offer.currency, spec.region_code)
        for offer, row, spec in lots
        if "COLOUR-WISE" in row.source_ref
    }
    markets = {"EU-UK": "EU", "UK": "UK", "INDIA": "INDIA", None: None}
    for make, model, colour, qty, price, _, _, _, spec in _sheet(BRAND_NEW, 0)[2:]:
        if not make:
            continue
        key = (model.strip(), _norm(colour))
        assert key in found, f"missing: {key}"
        assert found[key] == (qty, price, "GBP", markets[spec]), (key, found[key])


def test_every_incoming_line_is_on_the_board_as_incoming(board):
    lots, *_ = board
    found = {
        (row.description.strip(), _norm(row.colour)): (offer.quantity, offer.unit_price,
                                                        spec.availability,
                                                        row.availability_note)
        for offer, row, spec in lots
        if "INCOMING" in row.source_ref
    }
    for _make, model, colour, qty, price, *_ in _sheet(INCOMING)[2:]:
        if not model:
            continue
        key = (model.strip(), _norm(colour))
        assert found.get(key) == (qty, price, "incoming", "ETA 7-15 DAYS"), (key, found.get(key))


def test_every_lot_has_a_brand_and_a_model(board):
    lots, *_ = board
    unnamed = [row.description for _, row, spec in lots if not spec.brand or not spec.model_key]
    assert unnamed == []


def test_every_samsung_phone_line_is_categorised(board):
    lots, *_ = board
    uncategorised = [
        row.description for _, row, spec in lots
        if "GRADING" in row.source_ref and spec.category != "phone"
    ]
    assert uncategorised == []
