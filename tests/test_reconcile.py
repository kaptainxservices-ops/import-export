"""Daily list reconciliation.

Reconciliation is the one place where a mistake is silent and expensive. A wrong
insertion is noise on a screen; a wrong closure removes real stock from the board and
nobody ever learns the deal was there. Most of these tests are about refusing to close.
"""

from datetime import datetime, timedelta

import pytest

from app.brain.reconcile import (
    ExistingOffer,
    IncomingOffer,
    reconcile,
)

NOW = datetime(2026, 8, 7, 9, 0)


def live(key, qty=40, price=905.0, currency="USD", offer_id=None):
    return ExistingOffer(
        id=offer_id or f"id-{key}",
        identity_key=key,
        quantity=qty,
        unit_price=price,
        currency=currency,
        status="live",
        last_confirmed_at=NOW - timedelta(days=1),
    )


def arriving(key, qty=40, price=905.0, currency="USD"):
    return IncomingOffer(
        identity_key=key, quantity=qty, unit_price=price, currency=currency
    )


COMPLETE = {"is_complete_list": True}


# ---------------------------------------------------------------- the four cases


def test_identical_row_only_refreshes():
    """Nothing changed, so nothing should be written except the timestamp. Writing an
    update every morning would fill the history with 500 rows a day of no information."""
    result = reconcile([live("ean:1")], [arriving("ean:1")], **COMPLETE)

    assert result.refreshed == 1
    assert result.updated == 0
    assert result.actions[0].changes == []


def test_new_price_updates_and_logs_the_old_value():
    result = reconcile([live("ean:1", price=905.0)], [arriving("ean:1", price=890.0)], **COMPLETE)

    assert result.updated == 1
    change = result.actions[0].changes[0]
    assert change.field == "unit_price"
    assert change.old == "905.0"
    assert change.new == "890.0"


def test_same_lot_repriced_is_not_a_new_offer():
    """The whole reason price is excluded from identity. Included, every reprice would
    create a duplicate and the board would fill with dead rows."""
    result = reconcile([live("ean:1", price=905.0)], [arriving("ean:1", price=890.0)], **COMPLETE)
    assert result.inserted == 0


def test_quantity_change_is_recorded():
    result = reconcile([live("ean:1", qty=40)], [arriving("ean:1", qty=25)], **COMPLETE)
    assert [c.field for c in result.actions[0].changes] == ["quantity"]


def test_unseen_item_is_inserted():
    result = reconcile([live("ean:1")], [arriving("ean:1"), arriving("ean:2")], **COMPLETE)
    assert result.inserted == 1


def test_absent_item_is_closed():
    """The highest-value inference in the product: it sold, and no one had to say so."""
    result = reconcile([live("ean:1"), live("ean:2")], [arriving("ean:1")], **COMPLETE)

    assert result.closed == 1
    closure = next(a for a in result.actions if a.kind == "close")
    assert closure.identity_key == "ean:2"
    assert "sold" in closure.reason


def test_reappearance_reopens_rather_than_duplicating():
    sold = ExistingOffer(id="id-1", identity_key="ean:1", status="sold")
    result = reconcile([sold], [arriving("ean:1")], **COMPLETE)

    assert result.reopened == 1
    assert result.inserted == 0
    assert result.actions[0].offer_id == "id-1"


# ---------------------------------------------------------------- refusing to close


def test_undecided_completeness_closes_nothing():
    """A supplier who normally sends full lists will occasionally fire off 'just got 50
    more'. Reading that as complete would close everything else they stock."""
    result = reconcile([live("ean:1"), live("ean:2")], [arriving("ean:1")])

    assert result.closed == 0
    assert result.status == "flagged_partial_list"
    assert "undecided" in result.notes[0]


def test_partial_list_closes_nothing():
    result = reconcile(
        [live("ean:1"), live("ean:2")], [arriving("ean:1")], is_complete_list=False
    )
    assert result.closed == 0


def test_collapsed_row_count_closes_nothing():
    """The scenario: a sender's list normally yields 487 rows, one morning the
    attachment is malformed and 12 parse. Unguarded, this closes 475 live offers and
    raises no error anywhere."""
    existing = [live(f"ean:{i}") for i in range(487)]
    incoming = [arriving(f"ean:{i}") for i in range(12)]

    result = reconcile(existing, incoming, previous_row_count=487, **COMPLETE)

    assert result.closed == 0
    assert result.status == "flagged_low_row_count"
    assert "collapsed" in result.notes[0]
    # The rows that did arrive are still processed — they are harmless.
    assert result.refreshed == 12


def test_a_modest_drop_still_closes():
    """Suppliers do sell things. The guard must catch broken parses without blocking
    ordinary trading."""
    existing = [live(f"ean:{i}") for i in range(100)]
    incoming = [arriving(f"ean:{i}") for i in range(80)]

    result = reconcile(existing, incoming, previous_row_count=100, **COMPLETE)

    assert result.closed == 20
    assert result.status == "applied"


def test_empty_extraction_closes_nothing_and_fails():
    """An empty parse of a non-empty email is a failure, not an empty stock list."""
    result = reconcile([live("ean:1"), live("ean:2")], [], previous_row_count=2, **COMPLETE)

    assert result.closed == 0
    assert result.status == "failed"


def test_first_import_has_no_previous_count_and_still_works():
    result = reconcile([], [arriving("ean:1"), arriving("ean:2")], **COMPLETE)
    assert result.inserted == 2
    assert result.status == "applied"


# ---------------------------------------------------------------- suspicious changes


def test_thousandfold_price_move_is_flagged_for_review():
    """Exactly the shape of the number-format bug found in the samples: €1,079 read as
    €1.07. It does not look like an error, it looks like an extraordinary deal."""
    result = reconcile([live("ean:1", price=1079.0)], [arriving("ean:1", price=1.07)], **COMPLETE)

    action = result.actions[0]
    assert action.kind == "update"
    assert action.needs_review is True
    assert "parsing error" in action.reason
    assert result.needing_review == 1


def test_ordinary_reprice_is_not_flagged():
    result = reconcile([live("ean:1", price=905.0)], [arriving("ean:1", price=860.0)], **COMPLETE)
    assert result.actions[0].needs_review is False


def test_currency_change_is_flagged():
    """Suppliers rarely switch currency. It is usually a misread symbol, and a wrong
    currency does not present as an error — it presents as a good margin."""
    result = reconcile(
        [live("ean:1", price=905.0, currency="USD")],
        [arriving("ean:1", price=905.0, currency="EUR")],
        **COMPLETE,
    )
    assert result.actions[0].needs_review is True


# ---------------------------------------------------------------- duplicates


def test_duplicate_identity_in_one_list_is_not_double_counted():
    """The same product appears twice in one list — under two section headings, or once
    with an EAN and once without. Two rows describing one lot are not two lots."""
    result = reconcile([], [arriving("ean:1", qty=40), arriving("ean:1", qty=40)], **COMPLETE)

    assert result.inserted == 1
    assert result.duplicate_rows == 1
    assert "duplicate" in result.notes[0]


def test_duplicates_do_not_trip_the_row_count_guard():
    """Deduplication happens before the count check, so a list padded with repeats
    cannot make a genuinely short list look long enough to close on."""
    existing = [live(f"ean:{i}") for i in range(100)]
    incoming = [arriving("ean:0")] * 60

    result = reconcile(existing, incoming, previous_row_count=100, **COMPLETE)
    assert result.closed == 0
    assert result.status == "flagged_low_row_count"


# ---------------------------------------------------------------- a two-day run


def test_a_realistic_second_day():
    """One supplier, one night: one item repriced, one sold, one arrived, the rest
    unchanged."""
    existing = [
        live("ean:0195949035999", qty=50, price=579.0),   # unchanged
        live("ean:0195949036002", qty=45, price=679.0),   # repriced
        live("ean:0195949121296", qty=79, price=11.9),    # sold overnight
    ]
    incoming = [
        arriving("ean:0195949035999", qty=50, price=579.0),
        arriving("ean:0195949036002", qty=30, price=665.0),
        arriving("ean:0195950643701", qty=29, price=749.0),   # new
    ]

    result = reconcile(existing, incoming, previous_row_count=3, **COMPLETE)

    assert (result.refreshed, result.updated, result.inserted, result.closed) == (1, 1, 1, 1)
    assert result.status == "applied"
    assert result.needing_review == 0

    update = next(a for a in result.actions if a.kind == "update")
    assert {c.field for c in update.changes} == {"unit_price", "quantity"}


@pytest.mark.parametrize("bad", [None, []])
def test_never_raises_on_empty_input(bad):
    reconcile(bad or [], bad or [])
