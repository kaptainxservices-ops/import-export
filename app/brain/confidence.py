"""How much of a row we read, and how much we guessed.

A flagged row is one where at least one field fell below the threshold. It is held out of
the live order book until a human resolves it — so what counts as "below" decides both how
much manual work the client does and how much wrong data reaches his board. Set it too
strict and he reviews five hundred rows a morning, which the spec is explicit nobody will
do. Set it too loose and the queue is empty and the board is wrong.

The scoring is deterministic because the extraction is. This system does not ask a model
to emit rows with a self-reported confidence — it parses them with code, so the honest
question is not "how sure is the model" but **how much did we have to infer**. Those are
different questions and only the second one has a real answer here.

Three of the scores below are worth reading closely, because they encode the specific
mistakes that have actually reached the board during this build:

* An ambiguous thousands separator. '1,079' is read as 1079 by convention when a supplier
  has no configured format, and that convention is a guess. Reading €1.079 as €1.07 does
  not look like an error — it looks like an extraordinary margin, which is exactly the
  kind of number a broker acts on before checking.
* A price with no currency. An offer with no currency cannot be compared against anything,
  so it is worse than useless on a board whose entire purpose is comparison.
* A description that named no brand and no model code. If neither the catalogue nor a part
  number recognised it, nothing downstream will match it either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Below this, a row is held back. Chosen so that a clean table row scores 1.0 and sails
# through, while anything resting on an inference a human would want to see stops.
THRESHOLD = 0.7

# A quantity written as a range or an approximation: '200+', '5k pcs', '~50', '2 pallets'.
# Parsed to a number and kept, but the number is an interpretation.
# '\bk\b' does not match the k in '5k' — there is no word boundary between a digit and a
# letter, so the thousands shorthand has to be anchored to the digit instead.
_VAGUE_QUANTITY = re.compile(
    r"[+~]|\d\s*k\b|\bpallet|\bbox|\bcarton|\bapprox|\bup\s*to\b|\bmoq\b|-\s*\d", re.I
)

# Two or more digits with a single separator and exactly three trailing digits — the case
# that could be a thousand or could be a decimal, and nothing in the text settles it.
_AMBIGUOUS_GROUPING = re.compile(r"^\s*\d{1,3}[.,]\d{3}\s*$")


@dataclass
class Confidence:
    """Per-field scores, and what a person could actually do about the low ones.

    Confidence and reviewability are separate questions, and conflating them was a real
    mistake in the first version of this file: scored against the 45 sample emails it
    flagged **37% of all rows**, against a healthy ratio in the spec of 12 in 487. A queue
    holding a third of every list is a queue that gets approved blindly, which is worse
    than no queue at all.

    The two dominant causes were both things nobody can fix one row at a time — 1,103 rows
    with no currency and 1,323 whose product name matched nothing in the catalogue. Both
    are real problems. Neither is a *row* problem: the currency is one setting on the
    supplier, and the vocabulary is a per-sender mapping. Presenting them 2,400 times is
    presenting them in the wrong place.

    So the test for holding a row back is not "how sure are we" but **could a person
    looking at this single row fix it in a click**. Everything else is recorded, scored,
    and raised once against the whole import.
    """

    fields: dict[str, float] = field(default_factory=dict)
    # Fixable by editing this row: a vague quantity, an ambiguous separator.
    reasons: list[str] = field(default_factory=list)
    # Real, and fixed once for the supplier or the catalogue rather than per row.
    list_reasons: list[str] = field(default_factory=list)

    @property
    def overall(self) -> float:
        """The weakest field, not the average.

        A row with a perfect description and a guessed price is not 'mostly right'. It is
        a row with a guessed price, and averaging would hide exactly the field somebody
        needs to look at.
        """
        return min(self.fields.values(), default=1.0)

    @property
    def needs_review(self) -> bool:
        """Whether this row is held out of the live board for a human.

        Deliberately not `overall < THRESHOLD`. A low score means we inferred something;
        it does not follow that showing this row to somebody helps.
        """
        return bool(self.reasons)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def score_row(
    *,
    description: str,
    price: float | None,
    raw_price: str | None = None,
    currency: str | None = None,
    currency_source: str = "row",
    quantity: int | None = None,
    raw_quantity: str | None = None,
    brand: str | None = None,
    has_ean: bool = False,
    has_decimal_hint: bool = False,
    side: str = "sell",
) -> Confidence:
    """Score one extracted line.

    `currency_source` says where the currency came from — 'row', 'email' when the message
    declared one for the whole list, 'supplier' when it fell back to configuration, or
    'none'. The difference matters: a row that stated its own currency is a fact, and a
    row wearing its supplier's usual currency is an assumption that is usually right.
    """
    scored = Confidence()

    # ---------------------------------------------------------------- description
    words = [w for w in re.split(r"\W+", description or "") if len(w) > 1]
    if not description or not description.strip():
        scored.fields["description"] = 0.0
        scored.reasons.append("no product named")
    elif brand or has_ean:
        scored.fields["description"] = 1.0
    elif len(words) >= 3:
        # Readable, but nothing in it was recognised — it will sit on the board unable to
        # match anything. Raised against the import rather than the row: 1,323 of these
        # came from accessory lists, where a cable genuinely has no brand in the
        # catalogue, and no amount of staring at one row fixes that. The answer is a
        # learned vocabulary mapping for the sender, made once.
        scored.fields["description"] = 0.7
        scored.list_reasons.append("product name matched nothing in the catalogue")
    else:
        # One or two words is not a product. This one *is* row-level: a person reading it
        # next to the original email can usually say what it was meant to be.
        scored.fields["description"] = 0.4
        scored.reasons.append(f"description is only {len(words)} word(s)")

    # ---------------------------------------------------------------- price
    if price is None:
        if side == "buy":
            # A buyer's list states what they want, not what they will pay. Normal.
            scored.fields["price"] = 1.0
        else:
            scored.fields["price"] = 0.5
            scored.reasons.append("a seller's row with no price")
    elif raw_price and _AMBIGUOUS_GROUPING.match(raw_price) and not has_decimal_hint:
        # The one that costs a thousand times the money. '1,079' read as 1079 is a
        # convention, not a reading, and the supplier has not told us which they use.
        scored.fields["price"] = 0.45
        scored.reasons.append(
            f"{raw_price.strip()!r} could be {price:,.2f} or a thousandth of it — "
            "this supplier has no number format set"
        )
    else:
        scored.fields["price"] = 1.0

    # ---------------------------------------------------------------- currency
    if price is None:
        pass  # Nothing to denominate.
    elif not currency:
        # Serious — an offer with no currency cannot be compared against anything. But it
        # is one setting on the supplier, not a thousand row edits, so it is raised once
        # against the import and fixed on the Counterparties screen.
        scored.fields["currency"] = 0.3
        scored.list_reasons.append("no currency stated anywhere in this list")
    elif currency_source == "supplier":
        scored.fields["currency"] = 0.75
        scored.list_reasons.append(f"{currency} assumed from this supplier's usual")
    else:
        scored.fields["currency"] = 1.0

    # ---------------------------------------------------------------- quantity
    if quantity is None:
        # Common and survivable: the row still says what is for sale and what it costs.
        scored.fields["quantity"] = 0.85
    elif raw_quantity and _VAGUE_QUANTITY.search(raw_quantity):
        scored.fields["quantity"] = 0.6
        scored.reasons.append(f"{raw_quantity.strip()!r} read as {quantity}")
    else:
        scored.fields["quantity"] = 1.0

    return scored
