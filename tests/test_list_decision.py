"""The list-decision engine: replace, add, add new lines only, refresh, reject.

The rules decide; the checker (a model) is asked only when the rules lean towards
closing stock and are not sure, and it can only ever stop a closure. These tests pin
both halves, and the late-email cases that used to undo a newer list.
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.pipeline as pipeline
from app.brain.list_decision import (
    HEAVY_CLOSE_LINES,
    Lateness,
    keeps_row,
    lateness,
    read_wording,
    settle,
)
from app.brain.llm import listcheck
from app.brain.llm.client import Answer
from app.db.memory import InMemoryRepository
from app.db.models import ListedToday, TenantConfig
from app.pipeline import process_email
from app.schemas.email import Attachment, InboundEmail

UTC = timezone.utc  # noqa: UP017


# ================================================================ the rules, alone


class Spy:
    """A checker that records whether it was asked, and answers as told."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = 0

    def __call__(self, would_close):
        self.asked += 1
        return self.answer


def _settle(complete=True, wording="Price list", closes=3, live=100, check=None,
            why="rules"):
    return settle(
        rule_complete=complete, rule_why=why, wording=read_wording(wording, ""),
        would_close=closes, live_in_scope=live, check=check,
    )


@pytest.mark.parametrize("complete", [False, None])
def test_a_list_the_rules_call_partial_adds_and_the_checker_is_never_asked(complete):
    spy = Spy("complete")
    decision = _settle(complete=complete, wording="", check=spy)
    assert decision.action == "add"
    assert not decision.closes
    assert spy.asked == 0, "the checker must never be able to turn an add into a replace"


def test_a_clear_whole_list_closing_a_few_lines_replaces_without_asking():
    spy = Spy("addition")
    decision = _settle(wording="Daily price list", closes=4, live=200, check=spy)
    assert decision.action == "replace" and decision.is_complete is True
    assert spy.asked == 0
    assert decision.decided_by == "rules"


def test_a_whole_list_that_closes_nothing_is_never_checked():
    spy = Spy("addition")
    decision = _settle(wording="", closes=0, check=spy)
    assert decision.action == "replace"
    assert spy.asked == 0


@pytest.mark.parametrize("answer,action", [
    ("complete", "replace"), ("addition", "add"), (None, "add"),
])
def test_wording_that_points_both_ways_is_checked(answer, action):
    spy = Spy(answer)
    decision = _settle(wording="Price list - new arrivals", closes=5, check=spy)
    assert spy.asked == 1
    assert decision.action == action
    assert "both ways" in decision.note


@pytest.mark.parametrize("answer,action", [
    ("complete", "replace"), ("addition", "add"),
    # Nobody contradicts the rules: suppliers send whole lists, so a bare table at the
    # usual size is still read as one.
    (None, "replace"),
])
def test_a_bare_table_with_no_wording_is_checked(answer, action):
    spy = Spy(answer)
    decision = _settle(wording="", closes=5, live=200, check=spy)
    assert spy.asked == 1
    assert decision.action == action


def test_a_bare_table_with_no_model_configured_keeps_the_old_behaviour():
    decision = _settle(wording="", closes=5, live=200, check=None)
    assert decision.action == "replace"
    assert "checker unavailable" in decision.decided_by


@pytest.mark.parametrize("answer,action", [
    ("complete", "replace"), ("addition", "add"), (None, "add"),
])
def test_closing_a_large_share_is_checked_even_under_clear_wording(answer, action):
    spy = Spy(answer)
    decision = _settle(wording="Stock list", closes=60, live=100, check=spy)
    assert spy.asked == 1
    assert decision.action == action


def test_heavy_means_both_many_lines_and_a_large_share():
    many_but_small_share = _settle(wording="Stock list", closes=40, live=1000,
                                   check=Spy("addition"))
    assert many_but_small_share.action == "replace"
    large_share_but_few = _settle(wording="Stock list", closes=HEAVY_CLOSE_LINES - 1,
                                  live=12, check=Spy("addition"))
    assert large_share_but_few.action == "replace"


def test_heavy_closure_with_no_model_closes_nothing():
    decision = _settle(wording="Stock list", closes=80, live=100, check=None)
    assert decision.action == "add"
    assert not decision.closes
    assert "closed nothing" in decision.note


# ---------------------------------------------------------------- wording


@pytest.mark.parametrize("text,whole,extra", [
    ("PRICE LIST 12.10", True, False),
    ("WTS iPhone 16 Pro", True, False),
    ("Price list - new arrivals", True, True),
    ("Hi, some more stock below", False, True),
    ("Sorry, forgot these this morning", False, True),
    ("In addition to this morning's offer", False, True),
    ("Also available:", False, True),
    ("Please see attached", False, False),
])
def test_reading_the_covering_words(text, whole, extra):
    wording = read_wording(text, "")
    assert wording.says_whole is whole
    assert bool(wording.extra) is extra


# ---------------------------------------------------------------- late emails


MON = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
TUE = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)


def test_a_newer_whole_list_in_the_same_category_supersedes():
    found = lateness([ListedToday(TUE, True, ["phone"])], MON, {"phone"})
    assert found.superseded == {"phone"}
    assert not found.behind


def test_a_newer_whole_list_the_same_day_does_not_supersede():
    """'Today's Apple Watch offer' 07:45 and 'Today's Apple Accessories offer' 07:48,
    processed in the wrong order: both are today's stock."""
    watch = datetime(2026, 7, 27, 7, 45, tzinfo=UTC)
    accessories = datetime(2026, 7, 27, 7, 48, tzinfo=UTC)
    found = lateness([ListedToday(accessories, True, ["accessory", "watch"])], watch,
                     {"watch"})
    assert found.superseded == frozenset()
    assert found.behind


def test_the_day_boundary_is_the_clients_not_utc():
    # 23:30 and 00:30 UTC are 01:30 and 02:30 in Amsterdam (summer): the same day there.
    late_evening = datetime(2026, 7, 26, 23, 30, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 27, 0, 30, tzinfo=UTC)
    found = lateness([ListedToday(after_midnight, True, ["phone"])], late_evening,
                     {"phone"}, "Europe/Amsterdam")
    assert not found.superseded and found.behind
    # 21:30 and 22:30 UTC are either side of midnight in Amsterdam: different days.
    found = lateness([ListedToday(datetime(2026, 7, 26, 22, 30, tzinfo=UTC), True,
                                  ["phone"])],
                     datetime(2026, 7, 26, 21, 30, tzinfo=UTC), {"phone"},
                     "Europe/Amsterdam")
    assert found.superseded == {"phone"}


def test_a_newer_addition_only_limits_the_late_email_to_new_lines():
    found = lateness([ListedToday(TUE, False, ["phone"])], MON, {"phone"})
    assert found.superseded == frozenset()
    assert found.behind


def test_older_lists_and_other_categories_say_nothing_about_a_late_email():
    found = lateness(
        [ListedToday(MON - timedelta(days=1), True, ["phone"]),
         ListedToday(TUE, True, ["tablet"])],
        MON, {"phone"},
    )
    assert not found.late


def test_a_newer_list_from_before_categories_were_stored_covers_everything():
    found = lateness([ListedToday(TUE, True, None)], MON, {"phone", "tablet"})
    assert found.superseded == {"phone", "tablet"}


def test_a_sender_who_never_categorises_is_compared_on_their_uncategorised_lines():
    found = lateness([ListedToday(TUE, True, [""])], MON, {None})
    assert found.superseded == {None}


def test_an_email_arriving_at_the_same_moment_is_not_late():
    assert not lateness([ListedToday(MON, True, ["phone"])], MON, {"phone"}).late


def test_naive_timestamps_are_read_as_utc():
    found = lateness([ListedToday(TUE.replace(tzinfo=None), True, ["phone"])],
                     MON, {"phone"})
    assert found.superseded == {"phone"}


def test_strays_go_with_the_email_they_came_in():
    habits = {"phone", "tablet"}
    only_phones = frozenset({"phone"})
    assert not keeps_row("phone", habits, only_phones)
    assert keeps_row("tablet", habits, only_phones)
    # One adapter in a phone-and-tablet list stays while any of the list stands.
    assert keeps_row("accessory", habits, only_phones)
    assert keeps_row(None, habits, only_phones)
    everything = frozenset(habits)
    assert not keeps_row("accessory", habits, everything)
    assert not keeps_row(None, habits, everything)


def test_lateness_is_reported_as_late_only_when_it_matters():
    assert not Lateness().late
    assert Lateness(behind=True).late
    assert Lateness(superseded=frozenset({"phone"})).late


# ================================================================ the checker's format


def _answer(text):
    return Answer(text=text, model="test-model", input_tokens=10, output_tokens=1)


@pytest.mark.parametrize("reply,reading", [
    ("complete", "complete"), ("Addition.", "addition"), ("`complete`", "complete"),
    ("unclear", None),
    # A sentence is not one of the three words, and is not obeyed.
    ("I think this is complete", None), ("", None),
])
def test_the_checker_takes_exactly_one_of_three_words(monkeypatch, reply, reading):
    monkeypatch.setattr(listcheck, "available", lambda: True)
    monkeypatch.setattr(listcheck, "ask", lambda *a, **k: _answer(reply))
    found = listcheck.check_whole_list("Price list", "body", row_count=10, usual_rows=12,
                                       would_close=3)
    assert found is not None
    assert found.reading == reading


def test_the_checker_returns_none_when_the_model_is_down(monkeypatch):
    monkeypatch.setattr(listcheck, "available", lambda: True)
    monkeypatch.setattr(listcheck, "ask", lambda *a, **k: None)
    assert listcheck.check_whole_list("x", "y", row_count=1, usual_rows=None,
                                      would_close=1) is None


def test_the_checker_is_not_asked_without_a_model():
    assert listcheck.check_whole_list("x", "y", row_count=1, usual_rows=None,
                                      would_close=1) is None


def test_the_checker_is_told_the_sizes(monkeypatch):
    seen = {}

    def fake_ask(system, prompt, **kwargs):
        seen["prompt"] = prompt
        return _answer("complete")

    monkeypatch.setattr(listcheck, "available", lambda: True)
    monkeypatch.setattr(listcheck, "ask", fake_ask)
    listcheck.check_whole_list("Offer", "x" * 10_000, row_count=40, usual_rows=120,
                               would_close=80)
    assert "40 lines" in seen["prompt"] and "120 lines" in seen["prompt"]
    assert "80 of those" in seen["prompt"]
    assert len(seen["prompt"]) < 3000, "the table must not be sent whole"


# ================================================================ end to end


TENANT = TenantConfig(id="tenant-1", name="TVD", internal_domains={"tvdservices.com"},
                      timezone="Europe/Amsterdam")
HEADERS = ["Description", "Qty", "Price"]
SUPPLIER = "sales@whitehawk.example"


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT])


def send(repo, rows, *, at, mid, subject="Price list", body="Please see our list."):
    return process_email(
        InboundEmail(
            tenant_id="tenant-1", message_id=mid, from_email=SUPPLIER, subject=subject,
            received_at=at, body_text=body,
            attachments=[Attachment(filename="list.xlsx", kind="table",
                                    rows=[HEADERS, *rows])],
        ),
        repo,
    )


def board(repo):
    return {o.description: (o.quantity, o.unit_price) for o in repo.live_offers()}


def notes(repo):
    return [stored.record.notes for stored in repo.imports]


PHONES = [
    [f"Apple iPhone 16 {gb}GB {colour}", "10", str(700 + i)]
    for i, (gb, colour) in enumerate(
        (gb, colour) for gb in (128, 256, 512) for colour in ("Black", "White", "Pink")
    )
]


def _naive(moment):
    return moment.replace(tzinfo=None)


def test_mondays_list_processed_after_tuesdays_changes_nothing(repo):
    """n8n catches up after an outage and delivers Monday's list after Tuesday's. It
    used to be read as the first list of Monday and replaced Tuesday's board."""
    tuesday = PHONES[:6] + [["Apple iPhone 16 Pro 256GB Desert Titanium", "5", "990"]]
    send(repo, tuesday, at=_naive(TUE), mid="<tue@x>")
    before = board(repo)

    monday = [[d, q, str(int(p) - 50)] for d, q, p in PHONES]  # older, cheaper, all nine
    result = send(repo, monday, at=_naive(MON), mid="<mon@x>")

    assert result.outcome == "superseded"
    assert board(repo) == before, "an older list changed the board"
    assert repo.imports[-1].record.status == "superseded"
    assert "decision: reject" in notes(repo)[-1]


def test_a_late_list_behind_an_addition_only_adds_lines_the_board_never_had(repo):
    morning = datetime(2026, 10, 12, 7, 0)
    afternoon = datetime(2026, 10, 12, 15, 0)
    send(repo, PHONES[:6], at=morning, mid="<am@x>")
    # Afternoon addition, repricing one line.
    send(repo, [["Apple iPhone 16 128GB Black", "10", "650"]], at=afternoon,
         mid="<pm@x>", subject="Just arrived", body="Some more stock.")
    assert board(repo)["Apple iPhone 16 128GB Black"] == (10, 650)

    # A noon list arrives last: it carries the old price for the Black and two lines
    # the board has never had.
    noon = [["Apple iPhone 16 128GB Black", "10", "700"], *PHONES[6:8]]
    result = send(repo, noon, at=datetime(2026, 10, 12, 12, 0), mid="<noon@x>",
                  subject="Just arrived", body="Some more stock.")

    after = board(repo)
    assert after["Apple iPhone 16 128GB Black"] == (10, 650), "older price overwrote newer"
    assert PHONES[6][0] in after and PHONES[7][0] in after
    assert result.reconcile.closed == 0


def test_a_late_whole_list_behind_an_addition_closes_nothing(repo):
    send(repo, PHONES, at=datetime(2026, 10, 12, 7, 0), mid="<am@x>")
    send(repo, [["Apple iPhone 16 Pro 256GB Black Titanium", "3", "980"]],
         at=datetime(2026, 10, 12, 15, 0), mid="<pm@x>", subject="New arrivals")
    result = send(repo, PHONES[:2], at=datetime(2026, 10, 12, 12, 0), mid="<noon@x>",
                  subject="Price list")
    assert result.reconcile.closed == 0
    assert len(board(repo)) == len(PHONES) + 1
    assert "decision: add_new_only" in notes(repo)[-1]


def test_a_late_email_in_another_category_is_processed_normally(repo):
    send(repo, PHONES, at=_naive(TUE), mid="<tue@x>")
    tablets = [["Apple iPad Air 11 M3 128GB Wi-Fi Space Grey", "6", "520"],
               ["Samsung Galaxy Tab S10 FE 128GB Grey", "4", "380"]]
    result = send(repo, tablets, at=_naive(MON), mid="<mon@x>")
    assert result.outcome == "processed"
    assert "Samsung Galaxy Tab S10 FE 128GB Grey" in board(repo)


def test_every_applied_list_records_its_decision(repo):
    send(repo, PHONES, at=_naive(MON), mid="<mon@x>")
    send(repo, PHONES[:8], at=_naive(TUE), mid="<tue@x>")
    send(repo, PHONES[:1], at=_naive(TUE + timedelta(hours=3)), mid="<tue2@x>",
         subject="Just arrived")
    decisions = [n.split(" (")[0] for n in notes(repo)]
    assert decisions == ["decision: replace", "decision: replace", "decision: add"]


# ---------------------------------------------------------------- with the checker


@pytest.fixture
def model(monkeypatch):
    """A model that answers as the test says, and counts how often it is asked."""
    state = {"answer": "complete", "asked": 0}

    def fake(subject, body, **kwargs):
        state["asked"] += 1
        return listcheck.ListReading(state["answer"], _answer(state["answer"] or "unclear"))

    monkeypatch.setattr(pipeline, "model_available", lambda: True)
    monkeypatch.setattr(pipeline, "check_whole_list", fake)
    return state


def test_the_checker_stops_a_closure_the_wording_made_doubtful(repo, model):
    send(repo, PHONES, at=_naive(MON), mid="<mon@x>")
    model["answer"] = "addition"
    result = send(repo, PHONES[:7], at=_naive(TUE), mid="<tue@x>",
                  subject="Price list", body="New arrivals this week, see below.")
    assert model["asked"] == 1
    assert result.reconcile.closed == 0
    assert len(board(repo)) == len(PHONES)
    assert "overruled by the checker" in notes(repo)[-1]


def test_the_checker_confirms_a_doubtful_closure(repo, model):
    send(repo, PHONES, at=_naive(MON), mid="<mon@x>")
    model["answer"] = "complete"
    result = send(repo, PHONES[:7], at=_naive(TUE), mid="<tue@x>",
                  subject="Price list", body="New arrivals this week, see below.")
    assert result.reconcile.closed == 2
    assert "confirmed by the checker" in notes(repo)[-1]


def test_the_checker_is_not_asked_on_an_ordinary_day(repo, model):
    send(repo, PHONES, at=_naive(MON), mid="<mon@x>")
    send(repo, PHONES[:8], at=_naive(TUE), mid="<tue@x>", subject="Daily price list")
    assert model["asked"] == 0


def test_the_checker_is_billed_to_the_email(repo, model):
    send(repo, PHONES, at=_naive(MON), mid="<mon@x>")
    send(repo, PHONES[:7], at=_naive(TUE), mid="<tue@x>", subject="Price list",
         body="New arrivals.")
    assert [e.operation for e in repo.usage] == ["check"]


def test_the_checker_can_be_switched_off(repo, model, monkeypatch):
    monkeypatch.setenv("LIST_CHECKER_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        send(repo, PHONES, at=_naive(MON), mid="<mon@x>")
        send(repo, PHONES[:7], at=_naive(TUE), mid="<tue@x>", subject="Price list",
             body="New arrivals.")
        assert model["asked"] == 0
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- uncategorised senders


def test_an_uncategorised_senders_second_list_today_adds_and_tomorrow_replaces(repo):
    generic = [[f"Lot {n} mixed handsets", "10", str(100 + n)] for n in range(8)]
    send(repo, generic, at=_naive(MON), mid="<1@x>")
    second = send(repo, generic[:3], at=_naive(MON + timedelta(hours=4)), mid="<2@x>")
    assert second.reconcile.closed == 0
    tomorrow = send(repo, generic[:6], at=_naive(TUE), mid="<3@x>")
    assert tomorrow.reconcile.closed == 2


def test_two_lists_minutes_apart_processed_in_the_wrong_order_both_stand(repo):
    """The real pair: a watch list at 07:45 and an accessories list (with a few watches
    in it) at 07:48, delivered in the wrong order. The watch list used to be thrown away
    as 'older than the board'."""
    accessories = [
        ["Apple USB-C Power Adapter 20W", "500", "12"],
        ["Apple AirPods Pro 2 USB-C", "300", "190"],
        ["Apple Watch Series 10 GPS 42mm Jet Black Aluminium", "5", "300"],
        ["Apple Watch Series 10 GPS 46mm Jet Black Aluminium", "5", "320"],
    ]
    watches = [
        ["Apple Watch Series 10 GPS 42mm Jet Black Aluminium", "8", "310"],
        ["Apple Watch Series 10 GPS 46mm Silver Aluminium", "6", "325"],
        ["Apple Watch Ultra 2 49mm Black Titanium", "4", "690"],
        ["Apple Watch SE 2 GPS 40mm Midnight Aluminium", "9", "190"],
    ]
    send(repo, accessories, at=datetime(2026, 7, 27, 7, 48), mid="<acc@x>",
         subject="Today's Apple Accessories offer")
    result = send(repo, watches, at=datetime(2026, 7, 27, 7, 45), mid="<watch@x>",
                  subject="Today's Apple Watch offer")

    assert result.outcome == "processed"
    after = board(repo)
    assert "Apple Watch Ultra 2 49mm Black Titanium" in after
    assert "Apple Watch SE 2 GPS 40mm Midnight Aluminium" in after
    # The 42mm was on the newer list at 300; the older list's 310 does not overwrite it.
    assert after["Apple Watch Series 10 GPS 42mm Jet Black Aluminium"] == (5, 300)
    assert result.reconcile.closed == 0
    assert len(after) == 7


def test_a_one_line_uncategorised_wtb_is_an_order_for_a_buyer_with_many_open(repo):
    """Action, a buyer with forty-odd requirements, one of them a prose line nobody could
    categorise. A one-line 'WTB' compared against only that line looked like their whole
    list and closed it -- or not, depending on which email was processed first."""
    def wtb(rows, at, mid):
        return process_email(
            InboundEmail(
                tenant_id="tenant-1", message_id=mid, from_email="buy@action.example",
                subject="WTB Action", received_at=at, body_text="Please offer.",
                attachments=[Attachment(filename="l.xlsx", kind="table",
                                        rows=[["Description", "Qty"], *rows])],
            ),
            repo,
        )

    phones = [[f"Apple iPhone 16 {gb}GB {c}", "50"]
              for gb in (128, 256, 512) for c in ("Black", "White", "Pink", "Teal")]
    wtb([*phones, ["Lot of mixed handsets and tablets", "100"]],
        datetime(2026, 7, 27, 8, 36), "<a3@x>")
    result = wtb([["Assorted gaming consoles", "20"]], datetime(2026, 7, 27, 8, 54), "<a1@x>")

    assert result.reconcile.closed == 0
    assert "Lot of mixed handsets and tablets" in {o.description for o in repo.live_offers()}


# ---------------------------------------------------------------- what the trader sees


def test_a_stored_decision_reads_back():
    from app.brain.list_decision import ListDecision, parse_note

    decision = ListDecision("add", None, ("13 rows against a usual 40; too small",
                                          "checker could not tell; closed nothing"),
                            "rules; checker could not tell")
    stored = parse_note(decision.note + " 3 duplicate identities; completeness: x")
    assert stored.action == "add"
    assert stored.decided_by == "rules; checker could not tell"
    assert stored.reasons == decision.reasons
    assert stored.rest.startswith("3 duplicate")
    assert parse_note("completeness: an import from before decisions") is None
    assert parse_note(None) is None


@pytest.fixture
def client(repo):
    from fastapi.testclient import TestClient

    from app.api.deps import current_tenant
    from app.dependencies import set_repository
    from app.main import app

    set_repository(repo)
    app.dependency_overrides[current_tenant] = lambda: ("tenant-1", repo)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        set_repository(None)


def test_each_counterparty_shows_what_every_list_did(repo, client, model):
    send(repo, PHONES, at=_naive(MON), mid="<mon@x>")
    model["answer"] = "addition"
    send(repo, PHONES[:7], at=_naive(TUE), mid="<tue@x>", subject="Price list",
         body="New arrivals.")
    send(repo, PHONES[:3], at=_naive(MON - timedelta(days=1)), mid="<sun@x>")
    supplier = next(iter(repo.counterparties))

    answer = client.get(f"/suppliers/{supplier}/lists")
    assert answer.status_code == 200
    items = answer.json()["items"]
    assert [i["action"] for i in items] == ["add", "replace", "reject"]  # newest email first
    tuesday, monday, sunday = items
    assert tuesday["checked_by_model"] is True
    assert any("extra stock" in r for r in tuesday["reasons"])
    assert tuesday["closed"] == 0
    assert monday["added"] == len(PHONES) and monday["subject"] == "Price list"
    assert sunday["added"] == 0


def test_an_unknown_counterparty_has_no_lists(client):
    assert client.get("/suppliers/nobody/lists").status_code == 404
