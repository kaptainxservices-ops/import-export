"""Fetching every row, not just the first page.

PostgREST answers one request with one page, sized by the deployment's `db-max-rows`.
That setting is not ours and is not visible from the client, so a query that asks for
"everything" and trusts the answer is betting on it.

Losing that bet is not a short list. `load_live_offers` is what reconciliation compares
the incoming list against: a row missing from it looks like a row the supplier has
stopped offering, and closing exactly those rows is reconciliation's job. One supplier
on the live board already has 1,630 live offers. A 1,000-row page limit would read as
six hundred and thirty lots sold.

None of that can be tested against the real database from here, which is precisely why
the paging itself is tested against a stub.
"""

from __future__ import annotations

import pytest

from app.db.supabase_repo import _all_rows


class FakeQuery:
    """Enough of the PostgREST builder to answer `.range(...).execute()`."""

    def __init__(self, rows: list[dict], calls: list[tuple[int, int]]):
        self._rows = rows
        self._calls = calls
        self._start = 0
        self._end = 0

    def range(self, start: int, end: int):
        self._start, self._end = start, end
        self._calls.append((start, end))
        return self

    def execute(self):
        class Result:
            data = self._rows[self._start : self._end + 1]

        return Result()


def fake(total: int):
    """A table of `total` rows, plus the list of ranges that were asked for."""
    rows = [{"id": n} for n in range(total)]
    calls: list[tuple[int, int]] = []
    return (lambda: FakeQuery(rows, calls)), calls


def test_a_short_table_is_one_request():
    build, calls = fake(42)

    assert len(_all_rows(build, page=1000)) == 42
    assert len(calls) == 1


def test_a_table_longer_than_one_page_is_fetched_whole():
    """The case that matters: 1,630 rows behind a 1,000-row page."""
    build, calls = fake(1630)

    rows = _all_rows(build, page=1000)

    assert len(rows) == 1630
    assert [r["id"] for r in rows] == list(range(1630))
    assert calls == [(0, 999), (1000, 1999)]


def test_an_exact_multiple_does_not_lose_the_last_page():
    """Off-by-one country. 2,000 rows in pages of 1,000 needs a third request to learn
    there is nothing left, and stopping at two would silently drop nothing — until the
    day the table holds 2,001."""
    build, calls = fake(2000)

    assert len(_all_rows(build, page=1000)) == 2000
    assert len(calls) == 3


def test_an_empty_table_is_not_an_error():
    build, _ = fake(0)
    assert _all_rows(build, page=1000) == []


def test_the_safety_cap_stops_it(caplog):
    """Paging forever is not better than stopping. It must say so, loudly."""
    build, _ = fake(10_000)

    with caplog.at_level("ERROR"):
        rows = _all_rows(build, page=1000, cap=3000)

    assert len(rows) == 3000
    assert "incomplete" in caplog.text


@pytest.mark.parametrize("total", [0, 1, 999, 1000, 1001, 2500])
def test_every_row_arrives_whatever_the_size(total: int):
    build, _ = fake(total)
    assert [r["id"] for r in _all_rows(build, page=1000)] == list(range(total))
