"""Which side of the trade an email is on.

Getting this backwards is not a cosmetic error. A supplier's price list filed as buyer
demand has the client chasing people to sell them stock those people are themselves
selling, so the tests that matter here are the ones about refusing to decide.
"""

import pytest

from app.brain.classify import classify_side


def side(subject: str = "", body: str = "") -> str | None:
    return classify_side(subject, body).side


# ---------------------------------------------------------------- markers


@pytest.mark.parametrize(
    "subject",
    [
        "WTS Pricelist 24.07.2026",
        "Preisliste KW30",
        "Our stock list 06 Aug",
        "Prijslijst 7 juli",
        "Lagerbestand Samsung",
    ],
)
def test_a_seller_marker_settles_it(subject):
    assert classify_side(subject, "").confidence >= 0.9
    assert side(subject) == "sell"


@pytest.mark.parametrize(
    "subject",
    ["WTB Action", "Want to buy iPhone 15", "RFQ Apple", "Suche Samsung A56", "Ankauf"],
)
def test_a_buyer_marker_settles_it(subject):
    assert side(subject) == "buy"


def test_send_us_your_best_offer_is_a_buyer():
    """It contains the word 'offer' and it is a buyer. The marker wins over the noun,
    which is why the noun is only consulted when no marker matched."""
    assert side("Please send us your best offer for iPhone 16") == "buy"


def test_request_for_offer_is_a_buyer():
    assert side("Request for offer - iPhone 15 Pro") == "buy"


# ---------------------------------------------------------------- the convention


@pytest.mark.parametrize(
    "subject",
    [
        "All in Srl Offer",
        "Automic Offers",
        "Patktal Offer",
        "VNN International  - Offer",
        "Fwd: Special Offer",
        "Fwd: Today's Apple Watch offer",
    ],
)
def test_a_subject_titled_as_an_offer_is_a_seller(subject):
    """Straight from the client's inbox. He sorts on exactly this word — it is the name
    of the folder he sent half the samples in — and every one of these is a supplier
    offering stock. Reading the convention settled 17 of the 22 emails that previously
    needed a model."""
    assert side(subject) == "sell"
    assert classify_side(subject, "").confidence >= 0.9


@pytest.mark.parametrize("subject", ["Bauer Request", "New Way International  - Request"])
def test_a_subject_titled_as_a_request_is_a_buyer(subject):
    assert side(subject) == "buy"


def test_a_subject_naming_both_decides_nothing_on_its_own():
    """'Offer Request' is a request for an offer or an offer of a request, and the
    subject cannot tell you which."""
    assert classify_side("Offer / Request", "").needs_llm is True


# ---------------------------------------------------------------- refusal


def test_an_unmarked_email_is_left_undecided():
    """A forwarded WhatsApp message with no wording either way. Undecided must not be
    filed as either: a review queue costs a click, a wrong board costs the client."""
    result = classify_side("Fwd: message", "iPhone 13 128GB Black 410")
    assert result.side is None
    assert result.needs_llm is True


def test_a_body_signal_alone_is_not_treated_as_settled():
    """A buyer's signature reading 'price list available on request' matches a sell rule
    perfectly, and that email is not an offer. Body-only evidence stays below the bar
    that skips a second opinion."""
    result = classify_side("Re: yesterday", "our stock offer follows")
    assert result.side == "sell"
    assert result.confidence < 0.9
