"""A second opinion before a list closes stock the rules are not sure about.

The rules decide whether a list replaces what a sender had or adds to it. Most days they
are sure: the subject says 'PRICE LIST', the size is what this sender always sends. The
checker is asked only when they are not -- the wording points both ways ('Price list --
new arrivals'), there is no wording at all and the list would close lines, or the list
would close a large share of what the sender has live.

The checker can stop a closure. It can never cause one. Every answer it gives either
confirms what the rules already chose or moves the list to the safe side (add, close
nothing), so the worst a wrong answer can do is leave sold stock on the board until the
sender's next list -- never close stock that is still for sale.

Same shape as `side.py`: three words, nothing to invent, and 'unclear' is allowed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.brain.llm.client import Answer, ask, available

log = logging.getLogger(__name__)

Reading = Literal["complete", "addition"]

_ALLOWED = {"complete", "addition", "unclear"}

_SYSTEM = """You read emails in the wholesale mobile phone trade. A trader sent a list \
of stock they sell, or of stock they want to buy.

Decide whether this email is the sender's WHOLE current list, or an ADDITION to a list \
they sent before.

Answer with exactly one word, lowercase, nothing else:

  complete  this is everything they currently have (or want); anything missing from it \
is gone
  addition  this is extra -- new arrivals, a few more lots, a special offer, an update \
on some lines, an order for specific items
  unclear   you cannot tell

How the trade writes:
- 'Price list', 'stock list', 'our full offer', 'updated list', 'daily list' is complete.
- 'Just arrived', 'new arrivals', 'some more', 'additional', 'also available', 'forgot \
these', 'special offer', 'clearance', 'quick deal' is addition.
- A short note asking for specific items ('need 50 pcs of...') is addition.
- A bare table with no covering words, the same size as the sender usually sends, is \
usually complete.

Answer unclear rather than guessing. Calling an addition complete marks the sender's \
other stock as sold when it is not."""


@dataclass(frozen=True)
class ListReading:
    reading: Reading | None  # None: the model answered 'unclear' or broke the format
    answer: Answer


def check_whole_list(
    subject: str | None,
    body: str | None,
    *,
    row_count: int,
    usual_rows: int | None,
    would_close: int,
) -> ListReading | None:
    """Ask the model whether a list is the whole list. `None` means it was never asked."""
    if not available():
        return None

    subject = (subject or "").strip()
    # The covering words decide this, and they sit at the top. The table under them
    # says nothing about whether it is whole, so the body is cut hard.
    body = (body or "").strip()[:2500]

    facts = f"The list has {row_count} lines."
    if usual_rows:
        facts += f" This sender currently has {usual_rows} lines live in these categories."
    if would_close:
        facts += f" Read as complete, {would_close} of those would be marked gone."

    answer = ask(
        _SYSTEM,
        f"Subject: {subject or '(none)'}\n\n{body or '(empty body)'}\n\n---\n{facts}",
        role="classify",
        max_tokens=8,
    )
    if answer is None:
        return None

    word = answer.text.strip().strip("`\"'*. \n").lower()
    if word not in _ALLOWED:
        log.warning("list checker returned %r, not one of %s", answer.text, sorted(_ALLOWED))
        return ListReading(None, answer)
    return ListReading(None if word == "unclear" else word, answer)  # type: ignore[arg-type]
