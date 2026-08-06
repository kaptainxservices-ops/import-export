"""Counterparty attribution.

Offer identity is keyed on counterparty, and the disappearance rule compares today's
list against that supplier's previous one. Attribute an email to the wrong supplier and
two boards corrupt at once: one gains rows it never sent, the other has live stock
closed as sold.
"""

from app.brain.sender import resolve_sender

INTERNAL = {"tvdservices.com"}
INTERNAL_ADDRS = {"tvdservices@hotmail.com", "tvdlogistics@outlook.com"}


def resolve(envelope, subject="", body="", **kw):
    return resolve_sender(
        envelope, subject, body,
        internal_domains=kw.get("domains", INTERNAL),
        internal_addresses=kw.get("addresses", INTERNAL_ADDRS),
    )


# ------------------------------------------------------- 1. direct from supplier

def test_external_sender_is_taken_at_face_value():
    """The production case: suppliers mail the inbox directly."""
    r = resolve('"Buy - Belsimpel.nl" <buy@belsimpel.nl>', "WTB Phones")
    assert r.email == "buy@belsimpel.nl"
    assert r.method == "envelope"
    assert r.confidence == 1.0
    assert r.needs_review is False


def test_supplier_name_is_kept():
    r = resolve('"Team" <team@metro-trends.de>', "Brand New Stock")
    assert r.name == "Team"
    assert r.domain == "metro-trends.de"


# ------------------------------------------------------- 2. forwarded by staff

FORWARDED = """Begin forwarded message:
From: "Philip AB BUSINESS" <info@abbusiness.fr>
Subject: WANT TO SELL 27.07.2026
Date: 27 July 2026 at 1:23:27 am GMT+5:30
To: <sales@tvdservices.com>

WANT TO SELL
Samsung A165 Galaxy A16 128GB Black   109,50
"""


def test_forwarded_email_resolves_to_the_original_supplier():
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "Fwd: WANT TO SELL", FORWARDED)
    assert r.email == "info@abbusiness.fr"
    assert r.name == "Philip AB BUSINESS"
    assert r.method == "forwarded_header"
    assert r.needs_review is False


def test_outlook_style_forward_is_understood():
    body = """-----Original Message-----
From: Sales-Masterfone <sales@master-fone.com>
Sent: 27 July 2026 09:00

Price list attached.
"""
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "FW: Price List", body)
    assert r.email == "sales@master-fone.com"
    assert r.method == "forwarded_header"


def test_only_the_first_forward_block_is_read():
    """A long chain nests several From: lines; scanning the whole body would pick up
    whichever appeared last rather than the originator."""
    body = FORWARDED + "\n\n> On earlier date, From: someone@wrong-supplier.com wrote:"
    assert resolve("TVD SERVICES <sales@tvdservices.com>", "Fwd:", body).email == (
        "info@abbusiness.fr"
    )


# --------------------------------------- 3. relayed by hand (the WhatsApp route)

def test_hand_relayed_email_falls_back_to_a_body_address_and_flags_review():
    """A staff member pastes a WhatsApp offer into a fresh email. There is no
    forwarded header, so anything found is a suggestion, not an answer."""
    body = "Hello, please see below offer.\n\nRegards\nsales@reline-electronics.pl\n"
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "Reline Offer", body)
    assert r.email == "sales@reline-electronics.pl"
    assert r.method == "body_signature"
    assert r.needs_review is True
    assert r.confidence < 1.0


def test_free_mailboxes_are_not_treated_as_supplier_identities():
    body = "Offer below.\n\nRegards\nsomeguy@gmail.com"
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "Vadimpex Offer", body)
    assert r.method != "body_signature"


def test_noreply_and_unsubscribe_addresses_are_ignored():
    body = "Offer below.\nunsubscribe@mailing.example.com\nno-reply@example.com"
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "Some Offer", body)
    assert r.email not in {"unsubscribe@mailing.example.com", "no-reply@example.com"}


# ------------------------------------------------------- 4. subject only

def test_company_name_is_recovered_from_the_subject():
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "VNN International - Offer", "hi")
    assert r.name == "VNN International"
    assert r.method == "subject_company"
    assert r.needs_review is True


def test_trailing_date_is_stripped_from_the_company_name():
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "Smalltronic Offer 27.07.2026", "hi")
    assert r.name == "Smalltronic"


def test_request_suffix_is_handled_too():
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "New Way International  - Request", "x")
    assert r.name == "New Way International"


# ------------------------------------------------------- 5. nothing at all

def test_unresolvable_email_is_flagged_not_guessed():
    r = resolve("TVD SERVICES <sales@tvdservices.com>", "Hello", "Please see attached.")
    assert r.email is None
    assert r.method == "unresolved"
    assert r.needs_review is True


def test_internal_personal_mailboxes_count_as_internal():
    """Staff also send from personal addresses; those are configured per tenant."""
    r = resolve("Sameer Shah <tvdservices@hotmail.com>", "CELLULAR IBERIA OFFER", FORWARDED)
    assert r.email == "info@abbusiness.fr"


def test_never_raises_on_missing_input():
    assert resolve_sender(None, None, None).method == "unresolved"
