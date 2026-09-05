"""Cutting quoted history without cutting the email.

The balance here is set by the corpus rather than by general email etiquette. Of 43
sample emails with a plain-text body, 28 contain 'Begin forwarded message' and only 3
contain reply quoting at all — because forwarding a supplier's email to himself is how
the client works. A stripper tuned for a normal mailbox would delete the contents of two
thirds of these to tidy up three.

So most of these tests are about what must survive.
"""

from app.brain.quoting import strip_quoted_history

PRICES = """Apple iPhone 15 128GB Black 555,00 €
Samsung A16 128GB 105,00 €
Xiaomi 14 Ultra 512GB 609,00 €"""


# ---------------------------------------------------------------- what survives


def test_a_forwarded_supplier_list_is_kept_whole():
    """The dominant shape in this inbox. The payload is below the marker, so a stripper
    that treats a forward like a quote deletes the entire email."""
    body = f"""FYI

Begin forwarded message:

From: sales@supplier.example
Subject: WTS Pricelist 24.07

{PRICES}"""

    kept, why = strip_quoted_history(body)
    assert kept == body
    assert "555,00" in kept


def test_a_forward_wrapped_inside_a_reply_is_kept_whole():
    """Both markers present, forward second. Cutting at the reply marker would take the
    forwarded list with it."""
    body = f"""Please see below.

On Tue, 5 Aug 2026 at 09:14, Colleague <a@tvdservices.com> wrote:

---------- Forwarded message ---------
From: sales@supplier.example

{PRICES}"""

    kept, why = strip_quoted_history(body)
    assert kept == body
    assert "forwarded message sits inside" in why


def test_a_cut_that_would_remove_the_prices_is_refused():
    """The check that makes being wrong about a marker survivable.

    Rather than classifying every marker correctly, look at what the cut throws away.
    If the prices are on the far side of it, that was not the top of a quote — whatever
    it looked like.
    """
    body = f"""Hi,

-----Original Message-----

{PRICES}"""

    kept, why = strip_quoted_history(body)
    assert kept == body
    assert "more prices" in why


def test_a_body_with_no_quoting_is_untouched():
    kept, why = strip_quoted_history(PRICES)
    assert kept == PRICES
    assert why == "no quoted reply found"


def test_an_empty_body_is_survivable():
    assert strip_quoted_history("")[0] == ""
    assert strip_quoted_history(None)[0] == ""
    assert strip_quoted_history("   ")[0] == "   "


def test_a_single_stray_angle_bracket_is_not_a_conversation():
    """One '>' is a stray character. It takes a block of them to be a quote."""
    body = f"> note: prices valid today only\n\n{PRICES}"
    assert strip_quoted_history(body)[0] == body


# ---------------------------------------------------------------- what goes


def test_a_genuine_reply_quote_is_dropped():
    """Last week's list, quoted under this week's. Left in, the reconciler reads those
    as live offers and the board carries prices nobody is offering."""
    body = f"""{PRICES}

On Tue, 29 Jul 2026 at 08:02, Supplier <sales@supplier.example> wrote:

Apple iPhone 15 128GB Black 599,00 €
Samsung A16 128GB 129,00 €"""

    kept, why = strip_quoted_history(body)
    assert "599,00" not in kept
    assert "555,00" in kept
    assert "dropped" in why


def test_a_quoted_block_is_dropped():
    body = f"""{PRICES}

> Apple iPhone 15 128GB Black 599,00 €
> Samsung A16 128GB 129,00 €
> Xiaomi 14 Ultra 512GB 649,00 €"""

    kept, _ = strip_quoted_history(body)
    assert "599,00" not in kept
    assert "555,00" in kept


def test_stripping_twice_changes_nothing():
    """n8n is meant to have done this already, so the common case is running it over a
    body that is already clean."""
    body = f"{PRICES}\n\nOn Tue, 29 Jul 2026 at 08:02, S <s@x.example> wrote:\n\nold 1,00 €"

    once, _ = strip_quoted_history(body)
    twice, why = strip_quoted_history(once)

    assert twice == once
    assert why == "no quoted reply found"
