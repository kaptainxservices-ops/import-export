"""Model normalisation.

Every string here is the kind of thing a supplier actually types. Model is part of
offer identity, so a miss does not merely look untidy — '15PM' and 'iPhone 15 Pro Max'
resolving differently means the same lot appears twice on the board at two prices.
"""

import pytest

from app.brain.normalise import normalise_model


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The trade shorthand that a lookup table would miss
        ("15PM", "iPhone 15 Pro Max"),
        ("15 PM", "iPhone 15 Pro Max"),
        ("iphone15promax", "iPhone 15 Pro Max"),
        ("iPhone 15 Pro Max", "iPhone 15 Pro Max"),
        ("IP 15 PRO MAX", "iPhone 15 Pro Max"),
        ("i phone 15 pro max", "iPhone 15 Pro Max"),
        ("15 pro-max", "iPhone 15 Pro Max"),
        ("15 Max", "iPhone 15 Pro Max"),
        # Other variants
        ("iPhone 14 Pro", "iPhone 14 Pro"),
        ("14 plus", "iPhone 14 Plus"),
        ("iPhone 14+", "iPhone 14 Plus"),
        ("13 mini", "iPhone 13 mini"),
        ("iPhone 16e", "iPhone 16e"),
        ("iPhone 11", "iPhone 11"),
        ("iPhone 8 Plus", "iPhone 8 Plus"),
        # The X generation, still traded in volume
        ("iPhone XS Max", "iPhone XS Max"),
        ("XS MAX", "iPhone XS Max"),
        ("xsmax", "iPhone XS Max"),
        ("iPhone XR", "iPhone XR"),
        ("iphone x", "iPhone X"),
        # Air
        ("iPhone Air", "iPhone Air"),
    ],
)
def test_recognised_models(raw, expected):
    assert normalise_model(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The bug this guards: storage is stripped before the model number is read.
        # Without it, 'iPhone 12 64GB' parses as an iPhone 64.
        ("iPhone 12 64GB", "iPhone 12"),
        ("iPhone 15 Pro Max 256GB", "iPhone 15 Pro Max"),
        ("15PM 1TB Natural Titanium", "iPhone 15 Pro Max"),
        ("iPhone 13 128 GB Midnight", "iPhone 13"),
    ],
)
def test_storage_never_becomes_the_model_number(raw, expected):
    assert normalise_model(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "AirPods Pro 2nd gen",
        "Samsung Galaxy S24 Ultra",
        "MacBook Pro 14",
        "assorted accessories",
    ],
)
def test_returns_none_rather_than_guessing(raw):
    """A non-iPhone must not be coerced onto an iPhone board."""
    assert normalise_model(raw) is None


def test_full_line_item_from_a_real_style_list():
    line = "40x iPhone 15 Pro Max 256GB Blue Titanium A grade LL/A @ 905 USD"
    assert normalise_model(line) == "iPhone 15 Pro Max"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # '40x' is a quantity, not an iPhone X. This mislabelled an entire line once.
        ("40x iPhone 15 Pro Max", "iPhone 15 Pro Max"),
        ("100 x iPhone 14 Pro", "iPhone 14 Pro"),
        ("iPhone 15 x40", "iPhone 15"),
        # ...but a real iPhone X still resolves.
        ("iPhone X", "iPhone X"),
        ("20x iPhone X 64GB", "iPhone X"),
    ],
)
def test_quantity_marker_is_not_mistaken_for_the_iphone_x(raw, expected):
    assert normalise_model(raw) == expected


def test_never_raises_on_junk():
    """One bad cell in row 300 must not abandon the other 499 rows."""
    for junk in ["!!!", "15/15/15", "iPhone ", "()", "\n\t", "€€€", "15" * 50]:
        normalise_model(junk)
