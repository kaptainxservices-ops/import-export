"""Whether a list goes on the board, waits for a person, or needs neither.

Reconciliation reads a list as the supplier's complete current stock, which licences the
inference that makes the board self-maintaining: anything on yesterday's list and absent
from today's has sold. That holds for the first list of a day. It does not hold for the
second one.

A supplier sends the morning's stock, sells three lots, and sends it again at four. Read
as a complete list, the afternoon email closes every row it omits — and it omits plenty,
because half the time it is not a full list at all but 'here are the 40 iPhones I forgot
this morning'. Reconciling that closes the other 450.

So a second list the same day is not reconciled. It is parsed, stored, and held for a
person, who says whether it is the same list, a new list, or a partial update.

Except when it says exactly what the first one said, where there is nothing for a person
to decide — and that case is commoner than it sounds, because it is also what a
duplicate delivery looks like. One supplier email addressed to two staff is forwarded by
two collectors; when Outlook wrapped the forward it carries a new Message-ID, so
message-id dedupe never sees it, and the second copy arrives as a second list. Its rows
are identical by construction. The fingerprint catches both, and both want the same
thing: refresh the timestamps, reconcile nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

# timezone.utc rather than datetime.UTC: the alias only exists from 3.11, and the
# repository is run locally on 3.10. Matches app/db/supabase_repo.py.
from datetime import datetime, timezone  # noqa: UP017
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Verdict = str  # 'apply' | 'hold' | 'same'


@dataclass(frozen=True)
class PriorImport:
    """The last list from this counterparty and side that actually reached the board."""

    id: str
    created_at: datetime
    content_hash: str | None = None
    row_count: int = 0


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    compared_to: str | None = None


def local_day(moment: datetime, zone: str) -> str:
    """The calendar date a moment falls on, where the client stands.

    A naive datetime is read as UTC rather than as local time. Everything upstream
    stores UTC — n8n sends an ISO timestamp, Postgres hands back timestamptz — so
    assuming local here would silently shift the day boundary by an hour or two for
    exactly the evening emails where the boundary matters.
    """
    try:
        here = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        # A tenant with a typo'd zone must not stop their mail being processed. UTC is
        # within an hour or two of every zone this client trades in, so the worst case
        # is one evening list applied that should have been held.
        here = ZoneInfo("UTC")

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)  # noqa: UP017
    return moment.astimezone(here).date().isoformat()


def decide(
    *,
    arriving_at: datetime,
    content_hash: str,
    prior: PriorImport | None,
    tenant_timezone: str = "Europe/Amsterdam",
) -> Decision:
    """What to do with a list that has been parsed but not yet applied.

    `prior` is the most recent *applied* import for this counterparty and side — not
    merely the most recent one. A list that was held and never resolved has changed
    nothing about the board, so it must not stand in for what the board holds, and it
    must not make the next list look like a third.
    """
    if prior is None:
        return Decision("apply", "nothing from this counterparty and side yet")

    today = local_day(arriving_at, tenant_timezone)
    that_day = local_day(prior.created_at, tenant_timezone)

    if today != that_day:
        return Decision(
            "apply",
            f"first list of {today}; the one on the board is from {that_day}",
        )

    # Same day. Does it say anything new?
    if prior.content_hash and prior.content_hash == content_hash:
        return Decision(
            "same",
            "identical to the list already applied today",
            compared_to=prior.id,
        )

    if prior.content_hash is None:
        # An import from before fingerprinting, or one whose rows were never hashed.
        # Comparison is impossible, so fall back to the client's rule as stated: a
        # second list the same day waits for a person.
        return Decision(
            "hold",
            "a second list today, and the first cannot be compared against",
            compared_to=prior.id,
        )

    return Decision(
        "hold",
        "a second list today, and it differs from the one on the board",
        compared_to=prior.id,
    )
