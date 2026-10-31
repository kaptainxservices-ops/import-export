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
COMPLETE_SIGNALS = re.compile(
    r"\b(?:price\s*list|pricelist|preisliste|prijslijst|listino|lista\s*de\s*precios|"
    r"lista\s*preturi|liste\s*de\s*prix|stock\s*list|stocklist|stock\s*sheet|"
    r"lagerliste|lagerbestand|full\s*stock|current\s*stock|available\s*stock|"
    r"wts|want\s*to\s*sell|wtb|want\s*to\s*buy|daily\s*(?:list|offer|stock))\b",
    re.IGNORECASE,
)

# 'WTS' and 'WTB' head every message in this trade, a whole catalogue and a one-line
# "WTB 50x iPhone 15" alike. On a long list they mean the list; on a few lines they
# mean an order, and an order is an addition: reading "WTB 50x iPhone 15" as the buyer's
# complete demand closed every other requirement they had open.
TRADE_PREFIX_ONLY = re.compile(
    r"^(?:wts|want\s*to\s*sell|wtb|want\s*to\s*buy)$", re.IGNORECASE
)

# Wording that means "here is something extra".
PARTIAL_SIGNALS = re.compile(
    r"\b(?:just\s*(?:got|arrived|in)|new\s*arrivals?|newly\s*arrived|additional|"
    r"extra\s*stock|also\s*(?:have|available)|on\s*top\s*of|in\s*addition|"
    r"(?:some|few|a\s*few)\s*more|more\s*stock|forgot|missed\s*(?:these|this|out)|"
    r"special\s*offer|one\s*off|last\s*(?:few|units)|"
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

    partial_match = PARTIAL_SIGNALS.search(text)
    complete_match = COMPLETE_SIGNALS.search(text)

    # Wording wins over size. 'Special Offer' with 200 rows is still a supplement, and
    # a supplement's absences mean nothing.
    if partial_match and not complete_match:
        return False, f"wording suggests a supplement: {partial_match.group(0)!r}"

    # Whole-list wording with no supplement wording is believed, however short. A short
    # one is then the broken-attachment case -- 'PRICE LIST' and a third of the usual
    # rows -- and reconcile's row-count guard refuses the closures AND flags the import,
    # so a person sees it. Calling it undecided here would close nothing too, but
    # silently: a malformed sheet would never reach anyone's eyes.
    short_order = _only_trade_prefix(text, row_count, typical_row_count)
    if complete_match and not partial_match and not short_order:
        detail = f"subject or body says {complete_match.group(0)!r}"
        if typical_row_count:
            detail += f"; {row_count} rows against a usual {typical_row_count}"
        return True, detail

    if typical_row_count and row_count < typical_row_count * PARTIAL_RATIO:
        return None, (
            f"{row_count} rows against a usual {typical_row_count}; too small to treat "
            f"as a full list"
        )

    if _only_trade_prefix(text, row_count, typical_row_count):
        return None, (
            f"only {row_count} rows under {complete_match.group(0)!r}: an order or an "
            f"offer to add, not a whole list"
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


def _only_trade_prefix(text: str, row_count: int, typical: int | None) -> bool:
    """A few lines whose only 'whole list' wording is WTS or WTB: an order, not a list.

    Judged against the sender's own size. Two lines from a buyer who keeps three
    requirements open is their list; one line from a buyer with a hundred is an order.
    """
    found = list(COMPLETE_SIGNALS.finditer(text))
    return (
        bool(found)
        and row_count <= SMALL_LIST_ROWS
        and all(TRADE_PREFIX_ONLY.match(m.group(0)) for m in found)
        and (typical is None or row_count < typical * 0.5)
    )
