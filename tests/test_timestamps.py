"""Reading the timestamps PostgREST sends.

This looks like a formatting detail and is not. `last_applied_import` compares a stored
`created_at` against the arriving email to decide whether a list is the second one
today. The old helper returned `datetime.now()` for anything it could not parse — so a
timestamp with nine fractional digits would have made every prior import look like it
arrived moments ago, and every list from that counterparty would have been held for
review with no indication why.
"""

from datetime import datetime, timezone

import pytest

from app.db.supabase_repo import _parse_timestamp, _timestamp

# timezone.utc, not datetime.UTC: the alias is 3.11+ and this suite runs on 3.10.
NINE_AM = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)  # noqa: UP017


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-16T09:00:00+00:00", NINE_AM),
        # The trailing Z that fromisoformat rejected before 3.11.
        ("2026-10-16T09:00:00Z", NINE_AM),
        # Postgres sends whatever precision the column has; fromisoformat takes three
        # or six digits and nothing else.
        ("2026-10-16T09:00:00.123456789+00:00", NINE_AM.replace(microsecond=123456)),
        ("2026-10-16T09:00:00.12+00:00", NINE_AM.replace(microsecond=120000)),
        ("2026-10-16T09:00:00.123+0000", NINE_AM.replace(microsecond=123000)),
        ("2026-10-16 09:00:00+00", NINE_AM),
        ("2026-10-16T11:00:00+02:00", NINE_AM),
        (NINE_AM, NINE_AM),
    ],
)
def test_the_shapes_postgrest_actually_sends(raw, expected):
    assert _parse_timestamp(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "not a timestamp", "16/10/2026"])
def test_unreadable_is_none_not_a_guess(raw):
    """None is the answer a caller can act on. A guess is the one that hid the bug."""
    assert _parse_timestamp(raw) is None


def test_the_display_helper_still_never_raises():
    """Review cards and deal screens call this for something to render, and a missing
    timestamp there must not take the page down."""
    assert isinstance(_timestamp("nonsense"), datetime)
    assert isinstance(_timestamp(None), datetime)
