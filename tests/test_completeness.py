"""Full-list versus partial-list detection.

Every destructive action in reconciliation depends on this answer, so the tests care
most about the cases where it should refuse to say yes.

Subjects here are taken from the real sample emails.
"""

import pytest

from app.brain.completeness import detect_list_completeness


def judge(subject="", body="", rows=100, typical=None):
    return detect_list_completeness(subject, body, rows, typical)[0]


@pytest.mark.parametrize(
    "subject",
    [
        "PRICE LIST 24_07_2026",
        "Price List Masterfone 06-07-2026",
        "PRICELIST FROM 27.07.2026",
        "WANT TO SELL 27.07.2026",
        "WTS Pricelist 24.07.2026 - HITISY GmbH",
        "Metropolitan Trends GmbH - Brand New Stock - 24.07.2026",
        "MT-Lagerliste_24.07.2026",
        "WTS: today's stock list",
        "Yukatel today offer - WTS - Preisliste - price list - listino prezzi",
        "WTB Phoneport 20.07.2026",
    ],
)
def test_real_subjects_read_as_complete_lists(subject):
    assert judge(subject=subject, rows=200) is True


def test_supplement_wording_forbids_closing():
    """The one case this exists for: a daily-list supplier also sends 'just got 50 more'.
    Treating that as complete would close everything else they stock."""
    assert judge(subject="Just got 50 more 15PM", body="", rows=1) is False
    assert judge(body="We also have 20 units of iPhone 16 available", rows=2) is False


@pytest.mark.parametrize(
    "subject", ["New arrival - iPhone 17", "Additional stock available", "Quick deal - 40 units"]
)
def test_supplement_phrasings(subject):
    assert judge(subject=subject, rows=3) is False


def test_wording_beats_size():
    """'Special Offer' with 200 rows is still a supplement, and a supplement's absences
    mean nothing."""
    assert judge(subject="Special Offer", rows=200) is False


def test_a_tiny_list_with_no_wording_is_undecided():
    """Undecided and partial have the same consequence — nothing closes — because both
    mean absence proves nothing."""
    assert judge(subject="Offer", rows=2) is None


def test_far_below_the_usual_size_is_undecided():
    assert judge(subject="Stock", rows=30, typical=500) is None


def test_consistent_with_the_usual_size_is_complete():
    assert judge(subject="Stock", rows=245, typical=241) is True


def test_no_rows_is_undecided():
    assert judge(subject="Price list", rows=0) is None


def test_large_list_with_no_signals_leans_complete():
    """Suppliers send complete lists every time — confirmed with the client — so this
    is the sensible default. It is the weakest case and the reason is recorded."""
    is_complete, why = detect_list_completeness("Offer", "", 300, None)
    assert is_complete is True
    assert "assumed" in why


def test_the_reason_is_returned_for_the_audit_trail():
    """When the client asks why 40 units were closed this morning, 'the algorithm
    decided' is not an answer."""
    _, why = detect_list_completeness("PRICE LIST 24_07_2026", "", 245, 241)
    assert "price list" in why.lower()
    assert "245" in why


def test_never_raises_on_missing_input():
    detect_list_completeness(None, None, 0, None)
