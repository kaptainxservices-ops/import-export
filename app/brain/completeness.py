"""Is this email the supplier's full current stock, or a one-off?

Everything destructive in reconciliation hangs off this one question. Closure is only
licensed when a list is the sender's complete stock — because only then does absence
mean sold.

The client confirmed suppliers send the complete updated list every time, so the
default leans towards complete. But a supplier who sends a daily list will occasionally
also fire off "just got 50 more 15PMs", and reading that as a complete list would close
everything else they stock. That single case is what this exists for.

Three-valued on purpose. True licences closures, False forbids them, and **None —
undecided — also forbids them.** Being unsure and being sure it is partial have the
same consequence, because both mean absence proves nothing.
"""

from __future__ import annotations

import re

# Wording that means "here is everything I have".
_COMPLETE_SIGNALS = re.compile(
    r"\b(?:price\s*list|pricelist|preisliste|prijslijst|listino|lista\s*de\s*precios|"
    r"lista\s*preturi|liste\s*de\s*prix|stock\s*list|stocklist|stock\s*sheet|"
    r"lagerliste|lagerbestand|full\s*stock|current\s*stock|available\s*stock|"
    r"wts|want\s*to\s*sell|wtb|want\s*to\s*buy|daily\s*(?:list|offer|stock))\b",
    re.IGNORECASE,
)

# Wording that means "here is something extra".
_PARTIAL_SIGNALS = re.compile(
    r"\b(?:just\s*(?:got|arrived|in)|new\s*arrival|additional|extra\s*stock|"
    r"also\s*have|on\s*top\s*of|special\s*offer|one\s*off|last\s*(?:few|units)|"
    r"clearance|quick\s*deal|urgent\s*sale|nachschub|neu\s*eingetroffen)\b",
    re.IGNORECASE,
)

# A handful of rows is a note about a lot, not a catalogue.
SMALL_LIST_ROWS = 5

# Well below a sender's normal size suggests a fragment rather than a full list. This
# is a softer threshold than the destructive one in `reconcile`, which stops a broken
# parse from closing anything at all.
PARTIAL_RATIO = 0.4


def detect_list_completeness(
    subject: str | None,
    body: str | None,
    row_count: int,
    typical_row_count: int | None = None,
) -> tuple[bool | None, str]:
    """Return (is_complete, why).

    The reason is returned alongside so it can be stored on the import. When the client
    asks why 40 units were closed this morning, "the list was judged complete because
    the subject said 'PRICE LIST' and 245 rows arrived against a usual 241" is an
    answer. "The algorithm decided" is not.
    """
    text = f"{subject or ''}\n{body or ''}"

    if row_count == 0:
        return None, "no rows extracted"

    partial_match = _PARTIAL_SIGNALS.search(text)
    complete_match = _COMPLETE_SIGNALS.search(text)

    # Wording wins over size. 'Special Offer' with 200 rows is still a supplement, and
    # a supplement's absences mean nothing.
    if partial_match and not complete_match:
        return False, f"wording suggests a supplement: {partial_match.group(0)!r}"

    if typical_row_count and row_count < typical_row_count * PARTIAL_RATIO:
        return None, (
            f"{row_count} rows against a usual {typical_row_count}; too small to treat "
            f"as a full list"
        )

    if complete_match:
        detail = f"subject or body says {complete_match.group(0)!r}"
        if typical_row_count:
            detail += f"; {row_count} rows against a usual {typical_row_count}"
        return True, detail

    if row_count <= SMALL_LIST_ROWS:
        return None, f"only {row_count} rows and no wording either way"

    if typical_row_count and row_count >= typical_row_count * PARTIAL_RATIO:
        return True, f"{row_count} rows, consistent with a usual {typical_row_count}"

    # A large list with no signals either way. Suppliers send complete lists, so this
    # leans complete — but it is the weakest case, and it is recorded as such.
    return True, f"{row_count} rows and no wording either way; assumed complete"
