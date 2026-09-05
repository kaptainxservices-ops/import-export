"""Is this email offering stock, or asking for it — when the wording does not say.

Eleven of the 45 sample emails carry no WTS/WTB marker anywhere the rules can see: a
forwarded WhatsApp message a staff member retyped, a bare table with a two-word covering
note, a reply in a thread whose subject was set weeks ago. Those are the emails this
handles, and only those.

The answer space is three words wide. That is the safety property: there is nothing here
for a model to invent, only a choice among options that already existed, and one of the
options is 'I cannot tell'.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.brain.classify import Side
from app.brain.llm.client import Answer, ask, available

log = logging.getLogger(__name__)

_ALLOWED = {"sell", "buy", "unclear"}

_SYSTEM = """You classify emails in the wholesale mobile phone trade.

Answer with exactly one word, lowercase, nothing else:

  sell     the sender is offering stock they hold
  buy      the sender is asking for stock they want
  unclear  you cannot tell

How the trade writes:
- A price list, stock list, availability list or 'our offer' is sell.
- 'WTB', 'looking for', 'we need', a request for a quotation is buy.
- A signature reading 'we buy and sell all brands' says nothing about the message. \
Judge the message.
- A forwarded WhatsApp message pasted into an email is usually a supplier's offer.
- A table of products with prices and quantities, sent unprompted, is sell.
- A short list of models with no prices, or with target prices, is usually buy.

Answer unclear rather than guessing. A wrong answer files a supplier's catalogue as \
customer demand, and the client then chases people to sell them stock those people are \
themselves selling. No answer costs one click."""


@dataclass(frozen=True)
class SideVerdict:
    """What the model made of it, alongside what the call cost."""

    side: Side | None
    answer: Answer


def classify_side_llm(subject: str | None, body: str | None) -> SideVerdict | None:
    """Ask the model which side an email is. `None` means it was never asked."""
    if not available():
        return None

    subject = (subject or "").strip()
    # The intent is stated at the top or it is not stated at all. A supplier's 2,000-row
    # table adds nothing to the judgement and a great deal to the bill, so the body is
    # cut hard rather than summarised.
    body = (body or "").strip()[:3000]

    if not subject and not body:
        return None

    answer = ask(
        _SYSTEM,
        f"Subject: {subject or '(none)'}\n\n{body or '(empty body)'}",
        role="classify",
        # Room for one word. A model that needs more than that has not followed the
        # instruction, and the closed-set check below will refuse it anyway.
        max_tokens=8,
    )
    if answer is None:
        return None

    # Punctuation and quoting only. A model that wrapped the word in backticks followed
    # the instruction; one that wrote a sentence did not, and is refused below.
    word = answer.text.strip().strip("`\"'*. \n").lower()
    if word not in _ALLOWED:
        # Anything outside the three permitted words is treated as no answer. A model
        # that ignores the instruction is not a model to take a filing decision from.
        log.warning("classifier returned %r, which is not one of %s", answer.text, sorted(_ALLOWED))
        return SideVerdict(None, answer)

    return SideVerdict(None if word == "unclear" else word, answer)  # type: ignore[arg-type]
