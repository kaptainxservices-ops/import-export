"""Find what the database will refuse, without a database.

The reconciliation logic is tested end to end against an in-memory store, and all 45
sample emails pass through it. Eleven of them then fail on write to Supabase — so the
fault is not in the trading logic, it is in the shape of the values going into columns.
That failure is invisible to every existing test, because the in-memory store accepts
anything Python can hold and Postgres does not.

This runs the real `SupabaseRepository` against a client that never connects, and checks
every value it tries to send against the column types in supabase/migrations. An integer
column will not take 9,999,999,999; a char(3) will not take 'EURO'; an enum will not take
a word that is not in it.

    python scripts/check_writes.py
    python scripts/check_writes.py --samples samples --verbose

Exit code is 1 if anything would be rejected.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.supabase_repo import SupabaseRepository  # noqa: E402
from app.pipeline import process_email  # noqa: E402
from scripts.load_samples import read_email  # noqa: E402

TENANT = "00000000-0000-0000-0000-0000000000aa"

INT32_MAX = 2_147_483_647
INT32_MIN = -2_147_483_648

# Straight from supabase/migrations. Keep in step with them: a column type that changes
# here and not there turns this script into a source of false confidence.
INTEGER_COLUMNS = {
    "offers": ("quantity", "capacity_gb", "ram_gb"),
    "imports": (
        "row_count", "previous_row_count", "offers_inserted", "offers_updated",
        "offers_refreshed", "offers_closed", "rows_expanded_from_variants",
        "duplicate_rows",
    ),
    "usage_events": ("input_tokens", "output_tokens", "duration_ms"),
}

# column -> (precision, scale). numeric(12,2) holds ten digits before the point.
NUMERIC_COLUMNS = {
    "offers": {"unit_price": (12, 2), "confidence": (3, 2)},
    "emails": {"counterparty_confidence": (3, 2)},
    "usage_events": {"cost_usd": (10, 6)},
}

CHAR_COLUMNS = {
    "offers": {"currency": 3},
    "counterparties": {"default_currency": 3},
}

ENUM_COLUMNS = {
    "offers": {
        "side": {"sell", "buy"},
        "status": {"live", "sold", "withdrawn", "expired"},
        "price_basis": {"per_unit", "per_lot", "unknown"},
    },
    "emails": {
        "classification": {
            "seller_offer", "buyer_request", "both", "negotiation",
            "irrelevant", "unclassified",
        },
        "direction": {"inbound", "outbound"},
        "counterparty_method": {
            "envelope", "forwarded_header", "body_signature",
            "subject_company", "unresolved", "manual",
        },
    },
    "imports": {
        "status": {"applied", "flagged_low_row_count", "flagged_partial_list", "failed"},
    },
}

NOT_NULL = {
    "offers": ("tenant_id", "counterparty_id", "side"),
    "emails": ("tenant_id", "message_id", "from_email", "received_at"),
    "counterparties": ("tenant_id", "primary_email"),
}


class Rejection(Exception):
    """What Postgres would have said."""


def check_row(table: str, row: dict) -> None:
    for column in NOT_NULL.get(table, ()):
        if row.get(column) is None and column in row:
            raise Rejection(f"{table}.{column} is null, and the column is not null")

    for column in INTEGER_COLUMNS.get(table, ()):
        value = row.get(column)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise Rejection(f"{table}.{column} is integer, got {type(value).__name__} {value!r}")
        if not INT32_MIN <= value <= INT32_MAX:
            raise Rejection(f"{table}.{column} is integer, got {value} — out of range")

    for column, (precision, scale) in NUMERIC_COLUMNS.get(table, {}).items():
        value = row.get(column)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Rejection(f"{table}.{column} is numeric, got {type(value).__name__} {value!r}")
        limit = 10 ** (precision - scale)
        if abs(value) >= limit:
            raise Rejection(
                f"{table}.{column} is numeric({precision},{scale}), got {value} — "
                f"needs |value| < {limit}"
            )

    for column, width in CHAR_COLUMNS.get(table, {}).items():
        value = row.get(column)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > width:
            raise Rejection(f"{table}.{column} is char({width}), got {value!r}")

    for column, allowed in ENUM_COLUMNS.get(table, {}).items():
        value = row.get(column)
        if value is None or column not in row:
            continue
        if value not in allowed:
            raise Rejection(
                f"{table}.{column} is an enum, got {value!r} — "
                f"allowed: {', '.join(sorted(allowed))}"
            )


# ---------------------------------------------------------------- fake client


class _Result:
    def __init__(self, data: list[dict]):
        self.data = data
        self.count = len(data)


class _Query:
    """Just enough PostgREST to let the repository run. Every insert is checked."""

    def __init__(self, table: str, log: dict[str, int]):
        self.table = table
        self.log = log
        self.op = "select"
        self.rows: list[dict] = []

    def insert(self, rows):
        self.op = "insert"
        self.rows = rows if isinstance(rows, list) else [rows]
        for row in self.rows:
            check_row(self.table, row)
            self.log[self.table] += 1
        return self

    def update(self, values: dict):
        self.op = "update"
        check_row(self.table, values)
        return self

    def upsert(self, rows):
        return self.insert(rows)

    def select(self, *args, **kwargs):
        self.op = "select"
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, *args):
        return self

    def in_(self, *args):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args):
        return self

    def execute(self):
        if self.op == "insert":
            return _Result([{**row, "id": str(uuid4())} for row in self.rows])
        if self.table == "tenants":
            # The one read that has to return something, or the pipeline stops at
            # 'unknown tenant' and nothing downstream is exercised at all.
            return _Result([{"id": TENANT, "name": "offline", "staleness_hours": 24}])
        return _Result([])


class OfflineClient:
    def __init__(self):
        self.written: dict[str, int] = defaultdict(int)

    def table(self, name: str) -> _Query:
        return _Query(name, self.written)


# ---------------------------------------------------------------- run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", default="samples")
    parser.add_argument("--verbose", action="store_true", help="name every email checked")
    args = parser.parse_args()

    paths = sorted(Path(args.samples).rglob("*.eml"))
    if not paths:
        print(f"no .eml files under {args.samples}")
        return 1

    client = OfflineClient()
    repository = SupabaseRepository(client)

    rejected: list[tuple[str, str]] = []
    broke: list[tuple[str, str]] = []
    ok = 0

    for path in paths:
        try:
            result = process_email(read_email(path, TENANT), repository)
        except Rejection as error:
            rejected.append((path.name, str(error)))
            print(f"  {path.name[:44]:46} REJECTED  {str(error)[:90]}")
            continue
        except Exception as error:
            broke.append((path.name, f"{type(error).__name__}: {error}"))
            print(f"  {path.name[:44]:46} ERROR     {type(error).__name__}: {str(error)[:70]}")
            continue

        ok += 1
        if args.verbose:
            print(f"  {path.name[:44]:46} {result.outcome:20} {result.row_count:5} rows")

    print(f"\n{'=' * 74}")
    print(f"  {'emails accepted':24} {ok} of {len(paths)}")
    for table, count in sorted(client.written.items()):
        print(f"  {'rows into ' + table:24} {count}")

    for title, group in (("REJECTED BY THE SCHEMA", rejected), ("FAILED BEFORE THE WRITE", broke)):
        if not group:
            continue
        print(f"\n{'-' * 74}\n{title}, grouped by cause\n{'-' * 74}")
        grouped: dict[str, list[str]] = defaultdict(list)
        for name, detail in group:
            grouped[detail[:200]].append(name)
        for detail, names in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            print(f"\n  {len(names)} email(s):\n    {detail}")
            for name in names[:5]:
                print(f"      - {name[:62]}")
            if len(names) > 5:
                print(f"      … and {len(names) - 5} more")

    return 1 if (rejected or broke) else 0


if __name__ == "__main__":
    raise SystemExit(main())
