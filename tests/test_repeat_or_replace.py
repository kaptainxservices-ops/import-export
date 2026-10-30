"""Repeat orders and replacement lists: what a new list does to what is already there.

The rule, in one place:

- A **whole list** replaces the sender's previous list *in the categories it covers*,
  plus any category this sender has habitually sent in the same email. Absent lines in
  that scope are closed as sold (or, for a buyer, as no longer wanted).
- A **later whole list the same day** in a category already covered today adds and
  updates; it closes nothing. Tomorrow's first list does the closing.
- A **short order or offer** ("WTB 50x iPhone 15", "just got 40 more") adds; it closes
  nothing, whatever its subject says.
- A **repeat** of a line already on the board refreshes it (and takes the new quantity
  or price); it never makes a second row. A line that closed and is asked for again is
  reopened.
"""

from datetime import datetime

import pytest

from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.pipeline import process_email
from app.schemas.email import Attachment, InboundEmail

TENANT = TenantConfig(
    id="tenant-1", name="TVD", internal_domains={"tvdservices.com"},
    timezone="Europe/Amsterdam",
)
HEADERS = ["Description", "Qty", "Price"]
WTB_HEADERS = ["Description", "Qty"]

MON = datetime(2026, 10, 12, 8, 0)
MON_LATER = datetime(2026, 10, 12, 10, 0)
TUE = datetime(2026, 10, 13, 8, 0)
WED = datetime(2026, 10, 14, 8, 0)


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT])


def send(repo, rows, *, at, mid, sender="buy@mobielwerkt.example", subject="WTB list",
         body="Please see our list."):
    return process_email(
        InboundEmail(
            tenant_id="tenant-1", message_id=mid, from_email=sender, subject=subject,
            received_at=at, body_text=body,
            attachments=[Attachment(filename="list.xlsx", kind="table", rows=rows)],
        ),
        repo,
    )


def live(repo, side=None):
    return sorted(
        o.description for o in repo.live_offers() if side is None or o.side == side
    )


PHONES = [
    WTB_HEADERS,
    ["Apple iPhone 16 128GB Black", "200"],
    ["Apple iPhone 16 256GB Black", "100"],
    ["Samsung Galaxy S25 256GB Navy", "150"],
]
ACCESSORIES = [
    WTB_HEADERS,
    ["Apple USB-C Power Adapter 20W", "500"],
    ["Apple AirPods Pro 2 USB-C", "300"],
    ["Samsung 25W Travel Adapter", "400"],
]
TABLETS = [
    WTB_HEADERS,
    ["Apple iPad Air 11 M3 128GB Wi-Fi Space Grey", "60"],
    ["Samsung Galaxy Tab S10 FE 128GB Grey", "40"],
]


# ---------------------------------------------------------------- split lists


def test_three_category_lists_on_one_morning_all_stand(repo):
    """Mobielwerkt's Accessories, Phones and Tablets, as three emails. Each used to be
    read as replacing the one before -- 121 new, 70 gone -- and held for review."""
    for rows, mid, at in [
        (ACCESSORIES, "<a@x>", MON),
        (PHONES, "<p@x>", MON_LATER),
        (TABLETS, "<t@x>", MON_LATER.replace(minute=30)),
    ]:
        result = send(repo, rows, at=at, mid=mid)
        assert result.outcome == "processed"
        assert result.reconcile.closed == 0

    assert len(live(repo, "buy")) == 8
    assert repo.held_rows == {}


def test_tomorrows_phones_list_replaces_phones_and_leaves_the_rest(repo):
    send(repo, ACCESSORIES, at=MON, mid="<a@x>")
    send(repo, PHONES, at=MON_LATER, mid="<p@x>")

    # Tuesday: the S25 is no longer wanted; nothing said about accessories.
    phones_tue = [WTB_HEADERS, ["Apple iPhone 16 128GB Black", "200"],
                  ["Apple iPhone 16 256GB Black", "80"]]
    result = send(repo, phones_tue, at=TUE, mid="<p2@x>")

    assert result.reconcile.closed == 1
    wanted = live(repo, "buy")
    assert "Samsung Galaxy S25 256GB Navy" not in wanted
    assert "Apple USB-C Power Adapter 20W" in wanted, "a phones list closed accessories"
    assert len(wanted) == 5


# ---------------------------------------------------------------- one combined list


def test_a_combined_list_sender_still_has_absences_read_as_sold(repo):
    """The other habit: one list a day with everything in it. Their accessories went
    missing from today's list because they sold, not because today's list is phones."""
    combined = [
        HEADERS,
        ["Apple iPhone 16 128GB Black", "50", "679"],
        ["Apple iPhone 16 256GB Black", "40", "759"],
        ["Apple USB-C Power Adapter 20W", "300", "11.90"],
        ["Apple AirPods Pro 2 USB-C", "120", "189"],
    ]
    send(repo, combined, at=MON, mid="<c1@x>", sender="sales@seller.example",
         subject="WTS price list")

    phones_only = [HEADERS, ["Apple iPhone 16 128GB Black", "50", "679"],
                   ["Apple iPhone 16 256GB Black", "40", "759"]]
    result = send(repo, phones_only, at=TUE, mid="<c2@x>", sender="sales@seller.example",
                  subject="WTS price list")

    assert result.reconcile.closed == 2
    assert live(repo) == ["Apple iPhone 16 128GB Black", "Apple iPhone 16 256GB Black"]


def test_a_stray_line_goes_with_its_own_list_and_ties_nothing_else(repo):
    """One case inside a twenty-line phone list belongs to that list: when tomorrow's
    phone list no longer has it, it sold. But it is not a habit of listing accessories
    with phones, so the same phone list must not touch the supplier's separate
    accessories list."""
    phones = [[f"Apple iPhone 16 {cap}GB {colour}", "10", "700"]
              for cap in (128, 256) for colour in
              ("Black", "White", "Pink", "Teal", "Ultramarine")]
    phones += [[f"Samsung Galaxy S25 {cap}GB {colour}", "10", "650"]
               for cap in (128, 256) for colour in
               ("Navy", "Icyblue", "Mint", "Silver Shadow", "Coralred")]
    stray = ["Spigen Ultra Hybrid Case iPhone 16", "50", "9"]
    seller = dict(sender="sales@seller.example", subject="WTS price list")
    send(repo, [HEADERS, *phones, stray], at=MON, mid="<s1@x>", **seller)
    accessories = [HEADERS, ["Apple USB-C Power Adapter 20W", "300", "11.90"],
                   ["Apple AirPods Pro 2 USB-C", "120", "189"],
                   ["Samsung 25W Travel Adapter", "200", "8.50"]]
    send(repo, accessories, at=MON_LATER, mid="<s2@x>", **seller)

    result = send(repo, [HEADERS, *phones], at=TUE, mid="<s3@x>", **seller)

    assert result.reconcile.closed == 1
    board = live(repo)
    assert "Spigen Ultra Hybrid Case iPhone 16" not in board
    assert "Apple AirPods Pro 2 USB-C" in board, "a phones list closed the accessories list"
    assert len(board) == 23


def test_a_stray_in_the_accessories_list_is_not_closed_by_the_phones_list(repo):
    """The real case: Mobielwerkt's accessories list carried one line read as a phone (a
    Pixelsnap ring stand). Their phones list must not close it."""
    watches = [[f"Apple Watch Series 11 {size}mm {colour}", "20"]
               for size in (42, 46) for colour in ("Black", "Silver", "Rose Gold", "Slate")]
    watches += [[f"Apple Watch SE 3 {size}mm {colour}", "20"]
                for size in (40, 44) for colour in ("Midnight", "Starlight", "Silver")]
    accessories = [WTB_HEADERS, *watches, ["Apple USB-C Power Adapter 20W", "500"],
                   ["Samsung 25W Travel Adapter", "400"],
                   ["Google Pixel 10 128GB Obsidian", "5"]]
    send(repo, accessories, at=MON, mid="<a1@x>")
    send(repo, PHONES, at=TUE, mid="<p1@x>")

    assert "Google Pixel 10 128GB Obsidian" in live(repo)


# ---------------------------------------------------------------- short orders


def test_a_one_line_wtb_adds_to_a_buyers_requirements(repo):
    """'WTB' heads every message in this trade. On a few lines it is an order, not the
    buyer's whole demand, and it must not close their other requirements."""
    send(repo, PHONES, at=MON, mid="<p@x>")

    order = InboundEmail(
        tenant_id="tenant-1", message_id="<o@x>", from_email="buy@mobielwerkt.example",
        subject="WTB", received_at=TUE,
        body_text="WTB\n\nApple iPhone 17 Pro 256GB Cosmic Orange - 100pcs - €1050",
        attachments=[],
    )
    result = process_email(order, repo)

    assert result.outcome == "processed"
    assert result.reconcile.closed == 0
    assert len(live(repo, "buy")) == 4


def test_a_one_line_wts_adds_to_a_suppliers_stock(repo):
    stock = [HEADERS, ["Apple iPhone 16 128GB Black", "50", "679"],
             ["Apple iPhone 16 256GB Black", "40", "759"],
             ["Samsung Galaxy S25 256GB Navy", "30", "649"]]
    send(repo, stock, at=MON, mid="<w1@x>", sender="sales@seller.example",
         subject="WTS price list")

    one = [HEADERS, ["Apple iPhone 17 256GB Black", "25", "799"]]
    result = send(repo, one, at=TUE, mid="<w2@x>", sender="sales@seller.example",
                  subject="WTS", body="")

    assert result.reconcile.closed == 0
    assert len(live(repo)) == 4


def test_a_supplement_says_so_and_closes_nothing(repo):
    stock = [HEADERS, ["Apple iPhone 16 128GB Black", "50", "679"],
             ["Apple iPhone 16 256GB Black", "40", "759"],
             ["Samsung Galaxy S25 256GB Navy", "30", "649"],
             ["Samsung Galaxy S25 512GB Navy", "30", "749"],
             ["Google Pixel 10 128GB Obsidian", "30", "549"],
             ["Google Pixel 10 256GB Obsidian", "30", "609"]]
    send(repo, stock, at=MON, mid="<x1@x>", sender="sales@seller.example",
         subject="WTS price list")

    extra = [HEADERS, ["Apple iPhone 17 256GB Black", "25", "799"],
             ["Apple iPhone 17 256GB White", "25", "799"],
             ["Apple iPhone 17 512GB Black", "25", "929"],
             ["Apple iPhone 17 512GB White", "25", "929"],
             ["Apple iPhone 17 Pro 256GB Silver", "10", "1099"],
             ["Apple iPhone 17 Pro 256GB Cosmic Orange", "10", "1099"]]
    result = send(repo, extra, at=TUE, mid="<x2@x>", sender="sales@seller.example",
                  subject="Special offer - just arrived", body="New arrival, extra stock.")

    assert result.reconcile.closed == 0
    assert len(live(repo)) == 12


# ---------------------------------------------------------------- repeats


def test_a_repeat_order_is_one_requirement_with_the_new_quantity(repo):
    send(repo, PHONES, at=MON, mid="<p1@x>")
    again = [WTB_HEADERS, ["Apple iPhone 16 128GB Black", "300"],
             ["Apple iPhone 16 256GB Black", "100"],
             ["Samsung Galaxy S25 256GB Navy", "150"]]
    result = send(repo, again, at=TUE, mid="<p2@x>")

    assert result.reconcile.inserted == 0
    assert result.reconcile.updated == 1
    assert result.reconcile.refreshed == 2
    rows = [o for o in repo.live_offers() if o.description == "Apple iPhone 16 128GB Black"]
    assert len(rows) == 1 and rows[0].quantity == 300


def test_a_requirement_asked_for_again_after_closing_comes_back(repo):
    send(repo, PHONES, at=MON, mid="<p1@x>")
    without_s25 = [WTB_HEADERS, ["Apple iPhone 16 128GB Black", "200"],
                   ["Apple iPhone 16 256GB Black", "100"]]
    send(repo, without_s25, at=TUE, mid="<p2@x>")
    assert "Samsung Galaxy S25 256GB Navy" not in live(repo)

    result = send(repo, PHONES, at=WED, mid="<p3@x>")

    assert result.reconcile.reopened == 1
    assert "Samsung Galaxy S25 256GB Navy" in live(repo)
    assert len([o for o in repo.offers.values()
                if o.description == "Samsung Galaxy S25 256GB Navy"]) == 1


def test_the_same_list_twice_in_a_morning_changes_nothing(repo):
    """One email, two collectors, two Message-IDs: a duplicate delivery."""
    send(repo, PHONES, at=MON, mid="<p1@x>")
    again = send(repo, PHONES, at=MON_LATER, mid="<p1-copy@outlook>")

    assert again.outcome == "same_as_applied"
    assert len(live(repo)) == 3


# ---------------------------------------------------------------- undecided side


def test_a_known_supplier_with_no_wording_is_read_as_selling(repo):
    """'Samsung Update 10.07.2026' over a price table: no WTS, no WTB. From a sender
    whose every line on the board is stock, it is stock -- not a trip to Review."""
    stock = [HEADERS, ["Apple iPhone 16 128GB Black", "50", "679"],
             ["Apple iPhone 16 256GB Black", "40", "759"],
             ["Samsung Galaxy S25 256GB Navy", "30", "649"]]
    send(repo, stock, at=MON, mid="<k1@x>", sender="sales@seller.example",
         subject="WTS price list")

    update = [HEADERS, ["Samsung Galaxy S25 256GB Navy", "60", "639"]]
    result = send(repo, update, at=TUE, mid="<k2@x>", sender="sales@seller.example",
                  subject="Samsung Update 13.10.2026", body="")

    assert result.outcome == "processed"
    assert result.side == "sell"


def test_a_firm_that_buys_and_sells_gets_no_guess(repo):
    seller = dict(sender="trade@both.example")
    send(repo, [HEADERS, ["Apple iPhone 16 128GB Black", "50", "679"]], at=MON,
         mid="<b1@x>", subject="WTS price list", **seller)
    send(repo, [WTB_HEADERS, ["Samsung Galaxy S25 256GB Navy", "100"]], at=MON,
         mid="<b2@x>", subject="WTB", **seller)

    result = send(repo, [HEADERS, ["Google Pixel 10 128GB Obsidian", "30", "549"]],
                  at=TUE, mid="<b3@x>", subject="Update", body="", **seller)

    assert result.outcome == "unclassified"


def test_an_email_that_names_no_product_is_not_review_work(repo):
    """The building's lift notice, a sponsorship pitch, a shipping alert: undecided,
    because they are not trade at all. Kept, but never put in front of a person."""
    notice = InboundEmail(
        tenant_id="tenant-1", message_id="<lift@x>", from_email="facilities@tower.example",
        subject="Lift maintenance this Friday", received_at=MON,
        body_text="Dear tenants,\nThe service lift in Tower B will be out of action on "
                  "Friday between 09:00 and 13:00.\nRegards,\nFacilities",
        attachments=[],
    )
    result = process_email(notice, repo)

    assert result.outcome == "irrelevant"
    assert repo.list_review_queue("tenant-1") == []


def test_an_undecided_email_with_products_in_it_still_goes_to_a_person(repo):
    stock = InboundEmail(
        tenant_id="tenant-1", message_id="<s@x>", from_email="desk@newsender.example",
        subject="Update", received_at=MON,
        body_text="Hello,\nApple iPhone 17 Pro 256GB Deep Blue - 40 pcs - €1,050\n",
        attachments=[],
    )
    result = process_email(stock, repo)

    assert result.outcome in ("unclassified", "processed")
    if result.outcome == "unclassified":
        assert len(repo.list_review_queue("tenant-1")) == 1
