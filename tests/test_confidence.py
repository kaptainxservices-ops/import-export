"""What gets held back, and why.

A flagged row is kept out of the live order book until somebody resolves it, so this
threshold decides two things at once: how much manual work the client does every morning,
and how much wrong data reaches his board. The spec is explicit that nobody reviews five
hundred rows, so a clean table row has to sail through untouched — and the rows that do
stop have to be the ones a human would genuinely want to see.

Every low score below corresponds to a mistake that actually reached the board during this
build.
"""

from __future__ import annotations

import pytest

from app.brain.confidence import THRESHOLD, score_row


def clean(**overrides):
    base = dict(
        description="Apple iPhone 15 Pro Max 256GB Natural Titanium",
        price=905.0,
        raw_price="905.00",
        currency="USD",
        currency_source="row",
        quantity=40,
        raw_quantity="40",
        brand="Apple",
    )
    return score_row(**{**base, **overrides})


# ---------------------------------------------------------------- the common case


def test_a_clean_table_row_passes_untouched():
    """The whole design rests on this. If an ordinary row does not score 1.0, the client
    is reviewing hundreds a morning and will stop using the queue."""
    scored = clean()

    assert scored.overall == 1.0
    assert scored.needs_review is False
    assert scored.reasons == []


def test_a_missing_quantity_does_not_stop_a_row():
    """Common and survivable — the row still says what is for sale and what it costs."""
    scored = clean(quantity=None, raw_quantity=None)

    assert scored.needs_review is False


def test_a_buyers_row_without_a_price_is_normal():
    """A WTB list says what they want, not what they will pay. Flagging those would put
    every buyer requirement in the queue."""
    scored = clean(price=None, raw_price=None, currency=None, side="buy")

    assert scored.needs_review is False


# ---------------------------------------------------------------- what stops


def test_the_thousand_fold_ambiguity_is_flagged():
    """The single most dangerous guess in the system. '1,079' is read as 1079 by
    convention when the supplier has no configured format — and reading €1.079 as €1.07
    does not present as an error, it presents as an extraordinary margin."""
    scored = clean(price=1079.0, raw_price="1,079", has_decimal_hint=False)

    assert scored.needs_review is True
    assert "thousandth" in scored.reason
    assert "number format" in scored.reason


def test_the_same_row_passes_once_the_supplier_format_is_known():
    """Which is what the Counterparties screen is for. Setting the format converts a
    guess into a reading, and the row stops needing a human."""
    scored = clean(price=1079.0, raw_price="1,079", has_decimal_hint=True)

    assert scored.needs_review is False


def test_a_missing_currency_is_raised_against_the_list_not_the_row():
    """Serious, and not a row problem. An offer with no currency cannot be compared
    against anything — but it is one setting on the supplier, and presenting it 1,103
    times is presenting it in the wrong place."""
    scored = clean(currency=None, currency_source="none")

    assert scored.needs_review is False
    assert scored.fields["currency"] < THRESHOLD
    assert "no currency stated" in scored.list_reasons[0]


def test_a_currency_assumed_from_the_supplier_is_noted_but_allowed():
    """Usually right, and worth saying out loud. Not worth stopping the row for."""
    scored = clean(currency_source="supplier")

    assert scored.needs_review is False
    assert "assumed" in scored.list_reasons[0]
    assert scored.fields["currency"] < 1.0


def test_an_unrecognised_product_is_raised_against_the_list():
    """1,323 of these came from accessory lists, where a cable genuinely has no brand in
    the catalogue. Staring at one row does not fix it — a learned mapping for the sender
    does, made once."""
    scored = clean(description="Prodotto speciale rigenerato", brand=None)

    assert scored.needs_review is False
    assert "matched nothing in the catalogue" in scored.list_reasons[0]


def test_a_barcode_rescues_an_unrecognisable_description():
    """EAN is exact. If we have one, not recognising the words does not matter."""
    scored = clean(description="Artikel 44821", brand=None, has_ean=True)

    assert scored.needs_review is False


def test_a_sellers_row_with_no_price_is_flagged():
    scored = clean(price=None, raw_price=None, currency=None, side="sell")

    assert scored.needs_review is True
    assert "no price" in scored.reason


@pytest.mark.parametrize("raw", ["200+", "5k pcs", "~50", "2 pallets", "up to 100"])
def test_a_vague_quantity_is_flagged_with_what_it_was_read_as(raw):
    """Parsed and kept, per the spec, but the number is an interpretation and the trader
    is about to promise it to somebody."""
    scored = clean(quantity=200, raw_quantity=raw)

    assert scored.needs_review is True
    assert "read as 200" in scored.reason


def test_an_empty_description_scores_zero():
    assert clean(description="").overall == 0.0


# ---------------------------------------------------------------- how it scores


def test_the_weakest_field_decides_not_the_average():
    """A row with a perfect description and a guessed price is not 'mostly right'. It is
    a row with a guessed price, and averaging would hide the field somebody needs."""
    scored = clean(currency=None, currency_source="none")

    assert scored.fields["description"] == 1.0
    assert scored.overall == scored.fields["currency"]
    assert scored.overall < THRESHOLD


def test_a_low_score_does_not_by_itself_hold_a_row_back():
    """The distinction the whole file turns on. Scoring low says we inferred something;
    it does not follow that showing the row to somebody helps. Conflating the two flagged
    37% of every list, against a healthy ratio in the spec of 12 in 487."""
    scored = clean(currency=None, currency_source="none")

    assert scored.overall < THRESHOLD
    assert scored.needs_review is False
    assert scored.list_reasons


def test_every_flagged_row_says_why():
    """A queue that says 'flagged' and nothing else is a queue that gets approved
    blindly, which is worse than no queue."""
    for scored in (
        clean(price=1079.0, raw_price="1,079"),
        clean(quantity=200, raw_quantity="200+"),
        clean(description="Cable", brand=None),
        clean(price=None, raw_price=None, currency=None, side="sell"),
    ):
        assert scored.needs_review
        assert scored.reason
