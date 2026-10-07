"""Recognising a supplier by the name in a subject line.

About one email in five arrives with no address in it at all — an offer a staff member
retyped out of WhatsApp, with the supplier named only in the subject. The pipeline
refuses to invent an address, which is right, and sends it to a person, which is right
once and a tax by the twentieth time.

Matching on a name is riskier than matching on an address and the cost is asymmetric:
filing a catalogue against the wrong counterparty corrupts two boards at once, while
one more email in the review queue costs a click. Every test here leans that way — the
misses are all deliberate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from app.brain.companies import find_unique, is_distinctive, matches, tokens
from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.pipeline import process_email
from app.schemas.email import Attachment, InboundEmail


@dataclass
class Candidate:
    name: str | None
    primary_email: str = "x@example.test"
    id: str = "cp-1"


# ---------------------------------------------------------------- the rules


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Erregame S.p.A.", ["erregame"]),
        ("GOtel GmbH", ["gotel"]),
        ("A.M.D. Telekommunikations GmbH", ["telekommunikations"]),
        ("Inesa Zuber Smalltronic", ["inesa", "zuber", "smalltronic"]),
    ],
)
def test_legal_suffixes_are_not_part_of_the_name(name, expected):
    assert tokens(name) == expected


@pytest.mark.parametrize("name", ["Vadimpex", "Smalltronic", "Erregame", "Westech"])
def test_a_real_name_identifies_somebody(name):
    assert is_distinctive(name)


@pytest.mark.parametrize(
    "name",
    [
        "Team",            # a shared mailbox's display name
        "Sales",
        "International",   # describes half the trade
        "Trading Group",
        "GmbH",            # nothing but a legal form
        "",
        None,
    ],
)
def test_a_name_that_describes_half_the_trade_identifies_nobody(name):
    assert not is_distinctive(name)


def test_the_subject_name_may_be_shorter_than_the_stored_one():
    """Display names arrive as 'Inesa Zuber Smalltronic'; subjects say 'Smalltronic'."""
    assert matches("Smalltronic", "Inesa Zuber Smalltronic")


def test_it_does_not_match_the_other_way_round():
    """A stored 'Trinity' must not swallow a subject reading 'Trinity Electronics Poland'
    — that is a different company until somebody says otherwise."""
    assert not matches("Trinity Electronics Poland", "Trinity")


def test_two_candidates_is_not_a_near_miss():
    pool = [Candidate("Trinity Electronics GmbH"), Candidate("Trinity Electronics Poland")]
    assert find_unique("Trinity Electronics", pool) is None


def test_a_counterparty_with_no_name_matches_nothing():
    assert find_unique("Vadimpex", [Candidate(None), Candidate("")]) is None


# ---------------------------------------------------------------- through the pipeline

TENANT = TenantConfig(id="tenant-1", name="TVD", internal_domains={"tvdservices.com"})
OTHER = TenantConfig(id="tenant-2", name="Someone else")

ROWS = [
    ["Description", "Qty", "Price"],
    ["Apple iPhone 15 128GB Black", "50", "579"],
    ["Apple iPhone 16 128GB Teal", "45", "679"],
]


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT, OTHER])


def retyped(subject, *, tenant="tenant-1", message_id="<m1@x>"):
    """An offer with no address anywhere: pasted out of a chat by a staff member."""
    return InboundEmail(
        tenant_id=tenant,
        message_id=message_id,
        from_email="sales@tvdservices.com",   # internal — the staff member who pasted it
        subject=subject,
        received_at=datetime(2026, 8, 7, 9, 0),
        body_text="WTS\n\nApple iPhone 15 128GB Black 50 pcs",
        attachments=[Attachment(filename="stock.xlsx", kind="table", rows=ROWS)],
    )


def test_an_unknown_company_still_goes_to_a_person(repo):
    result = process_email(retyped("Vadimpex Offer"), repo)
    assert result.outcome == "needs_sender_review"


def test_a_company_somebody_already_identified_is_recognised(repo):
    """The whole point: answer once, not every morning."""
    repo.create_counterparty("tenant-1", "sales@vadimpex.example", "Vadimpex")

    result = process_email(retyped("Vadimpex Offer"), repo)

    assert result.processed
    assert result.row_count == 2


def test_the_match_is_recorded_as_a_name_match_not_an_address(repo):
    """So it stays possible to ask how much of the board rests on a name."""
    repo.create_counterparty("tenant-1", "sales@vadimpex.example", "Vadimpex")

    process_email(retyped("Vadimpex Offer"), repo)

    stored = next(iter(repo.emails.values()))
    assert stored.counterparty_method == "known_company"
    assert stored.counterparty_confidence == 0.75
    assert stored.needs_sender_review is False


def test_an_ambiguous_name_goes_to_a_person(repo):
    repo.create_counterparty("tenant-1", "a@trinity.example", "Trinity Electronics GmbH")
    repo.create_counterparty("tenant-1", "b@trinity.example", "Trinity Electronics Poland")

    assert process_email(retyped("Trinity Electronics Offer"), repo).outcome == (
        "needs_sender_review"
    )


def test_it_cannot_reach_another_clients_suppliers(repo):
    """The failure this must never have.

    Matching on a name rather than an address widens what a lookup can reach, and a
    lookup that crossed tenants would file one client's offers against another's
    supplier — visible on their board, under their supplier's name.
    """
    repo.create_counterparty("tenant-2", "sales@vadimpex.example", "Vadimpex")

    result = process_email(retyped("Vadimpex Offer"), repo)

    assert result.outcome == "needs_sender_review"
    assert result.counterparty_id is None


def test_a_real_address_still_wins(repo):
    """Name matching is a fallback, never an override."""
    repo.create_counterparty("tenant-1", "sales@vadimpex.example", "Vadimpex")

    direct = retyped("Vadimpex Offer").model_copy(
        update={"from_email": "someone@actuallysentit.example"}
    )
    process_email(direct, repo)

    stored = next(iter(repo.emails.values()))
    assert stored.counterparty_method == "envelope"
