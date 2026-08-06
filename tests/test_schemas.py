"""Offer identity — the rule the whole daily reconciliation rests on.

If identity is wrong, either every reprice creates a duplicate row, or two genuinely
different lots collapse into one. Both are quietly destructive, so they are pinned here.
"""

from app.schemas.offer import ExtractedLineItem


def _item(**overrides) -> ExtractedLineItem:
    base = {
        "side": "sell",
        "model": "iPhone 15 Pro Max",
        "storage_gb": 256,
        "colour": "Blue",
        "grade": "A",
        "region_code": "LL/A",
        "quantity": 40,
        "unit_price": 905.0,
        "currency": "USD",
    }
    return ExtractedLineItem(**{**base, **overrides})


def test_same_lot_at_a_new_price_keeps_its_identity():
    """The core case: a reprice updates a row, it does not create one."""
    assert _item().identity_key() == _item(unit_price=890.0, quantity=25).identity_key()


def test_identity_is_case_insensitive():
    """Senders are inconsistent about capitalisation between one day and the next."""
    assert _item().identity_key() == _item(colour="blue", grade="a").identity_key()


def test_different_storage_is_a_different_offer():
    assert _item().identity_key() != _item(storage_gb=512).identity_key()


def test_different_region_code_is_a_different_offer():
    """Region code is a major price driver — never collapse these together."""
    assert _item().identity_key() != _item(region_code="ZP/A").identity_key()


def test_buy_and_sell_never_collide():
    assert _item(side="sell").identity_key() != _item(side="buy").identity_key()


def test_confidence_is_bounded():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _item(confidence=1.5)
