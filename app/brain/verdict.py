"""How much a parsed price list should be trusted, at a glance.

Nobody reviews 500 rows. The summary is what does the work, so the summary has to be
honest about three different things, which look alike in a count and are not alike at
all:

    healthy   the parse looks like every other morning -- approve it
    check     something is worth a look, but the list is probably fine
    suspect   this does not look like a price list that parsed correctly

The distinction that matters is between *a few bad rows* and *a bad parse*. Twelve
flagged of 487 is a normal morning: approve, then fix the twelve. Three hundred and
forty of 392 is not forty times worse -- it is a different event, almost always the
sender changing their template so the columns misaligned, and the right action is to
reject the whole import rather than correct 340 rows by hand.

The row count against the sender's previous list catches the other failure, the one
flagged rows cannot see: a parse that read 30 rows of a 400-row list cleanly. Every row
it found is perfect. It is still a disaster, because reconciliation is about to treat
370 missing rows as stock that sold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Level = Literal["healthy", "check", "suspect"]

# Above this share of flagged rows the list stops being "a few bad rows" and becomes a
# misread list. Set from the sample corpus: a normal parse flags around 1%, and the
# template-drift case flags most of the list. Anything in between is rare enough that
# asking a person to look is the right answer.
_SUSPECT_FLAGGED_SHARE = 0.25

# A list this much smaller than the sender's last one is the silent failure: the rows
# that were read are fine, so nothing is flagged, but the ones that were missed are
# about to be reconciled as sold.
_SUSPECT_SHRINK = 0.5
_CHECK_SHRINK = 0.8


@dataclass(frozen=True)
class Verdict:
    level: Level
    reason: str


def judge_import(
    *,
    row_count: int,
    flagged_rows: int = 0,
    previous_row_count: int | None = None,
    parse_warnings: list[str] | None = None,
    status: str = "applied",
) -> Verdict:
    """One line a trader can act on without opening the list."""
    warnings = parse_warnings or []

    # The parser itself gave up. Nothing below is worth computing.
    if status.startswith("flagged_") or status == "failed":
        return Verdict("suspect", _STATUS_REASONS.get(status, f"the parser reported {status}"))

    if row_count == 0:
        return Verdict("suspect", "nothing was read from this email at all")

    share = flagged_rows / row_count if row_count else 0.0

    if share >= _SUSPECT_FLAGGED_SHARE:
        return Verdict(
            "suspect",
            f"{flagged_rows} of {row_count} rows need attention — that is usually the "
            f"sender changing their layout, not {flagged_rows} genuinely odd rows",
        )

    # Only meaningful once they have a history. A first list has nothing to shrink from,
    # and treating "no previous list" as a shrink would flag every new supplier.
    if previous_row_count:
        ratio = row_count / previous_row_count
        if ratio <= _SUSPECT_SHRINK:
            return Verdict(
                "suspect",
                f"{row_count} rows against {previous_row_count} last time — if that is "
                "a misread rather than a real change, the missing rows are about to be "
                "closed as sold",
            )
        if ratio <= _CHECK_SHRINK:
            return Verdict(
                "check",
                f"{row_count} rows, down from {previous_row_count} — smaller than usual "
                "but not alarming",
            )

    if flagged_rows:
        return Verdict(
            "check",
            f"{row_count} rows read, {flagged_rows} flagged — "
            f"approve the {row_count - flagged_rows} and fix the rest",
        )

    if warnings:
        # True of the list rather than of any row: no currency stated anywhere, product
        # names matching nothing. Fixed once on the supplier, not once per row.
        return Verdict("check", f"{row_count} rows read cleanly, but: {warnings[0]}")

    if previous_row_count:
        return Verdict(
            "healthy", f"{row_count} rows, in line with their usual {previous_row_count}"
        )

    return Verdict("healthy", f"{row_count} rows read cleanly — their first list")


_STATUS_REASONS = {
    "flagged_low_row_count": "far fewer rows than this sender usually sends; nothing was closed",
    "flagged_partial_list": "reads as an addition to a list rather than a replacement for it",
    "flagged_unparseable": "the table could not be read",
    "failed": "the import failed",
}
