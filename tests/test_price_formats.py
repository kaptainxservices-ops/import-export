"""European versus Anglo number formats.

Every example here is taken from the real sample emails. Both conventions appear in
the same inbox, sometimes in the same week, so this cannot be a global setting.

The failure mode is what makes these tests matter. Reading €1.079 as €1.07 does not
surface as an error anywhere — it surfaces as an extraordinary margin on the Matches
screen, which is exactly the kind of number a broker acts on before checking.
"""

import pytest

from app.brain.normalise import parse_price


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Metropolitan Trends, 709 prices in this format in one email
        ("2,50", 2.50),
        ("1,90", 1.90),
        ("109,50", 109.50),
        ("259,50", 259.50),
        ("1219,00", 1219.00),
        # Automic: dot as thousands separator
        ("€1.079", 1079.00),
        ("1.079", 1079.00),
        # Anglo
        ("1,234.50", 1234.50),
        ("12,500", 12500.00),
        ("905", 905.00),
        ("905.50", 905.50),
        # World Comm float artifact
        ("15.470519999999999", 15.470519999999999),
    ],
)
def test_real_world_price_formats(raw, expected):
    assert parse_price(raw) == pytest.approx(expected)


def test_the_thousand_fold_error_is_fixed():
    """The specific bug: €1.079 is €1,079, not €1.07."""
    assert parse_price("€1.079") == 1079.00
    assert parse_price("€1.079") != 1.07


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("€1.079", 1079.0), ("1.234.567", 1234567.0), ("2.500", 2500.0)],
)
def test_dot_as_thousands(raw, expected):
    assert parse_price(raw) == pytest.approx(expected)


def test_ambiguous_three_digit_group_reads_as_thousands():
    """'1,079' could be 1079 or 1.079. Four-figure prices are overwhelmingly more
    plausible in this trade than one-euro phones."""
    assert parse_price("1,079") == 1079.0


@pytest.mark.parametrize(
    ("raw", "hint", "expected"),
    [
        ("1,079", "comma", 1.079),
        ("1.079", "dot", 1.079),
        ("109,50", "comma", 109.50),
        ("1.234,56", "comma", 1234.56),
        ("1,234.56", "dot", 1234.56),
    ],
)
def test_supplier_hint_overrides_the_default(raw, hint, expected):
    """A supplier whose convention is known should never be guessed at."""
    assert parse_price(raw, decimal_hint=hint) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("€ 905", 905.0), ("USD 905.50", 905.5), ("905 EUR", 905.0), ("Price: 1.079 €", 1079.0)],
)
def test_currency_markers_are_ignored(raw, expected):
    assert parse_price(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "price on request", "TBC", "n/a", "-"])
def test_no_price_present(raw):
    assert parse_price(raw) is None


def test_never_raises_on_junk():
    for junk in [",", ".", ",.,", "1,,2", "....", "€", "1.2.3.4.5"]:
        parse_price(junk)
