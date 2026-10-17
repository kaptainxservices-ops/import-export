"""The Python side and the SQL side, held to the same vocabulary.

This is the gap that let fourteen emails fail. `reconcile()` can return an import status
of 'failed' and `public.import_status` had never heard of it — so the offers were written,
the import row was rejected, and the failure was invisible to every test in the suite,
because the in-memory store accepts any string Python can hold.

Nothing here needs a database. The migrations are read as text, which is the point: a
mismatch is caught by `pytest`, not at three in the morning by a client whose board has
stopped updating.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


def enum_values(name: str) -> set[str]:
    """Every label a Postgres enum has, across all migrations.

    Both forms count: the `create type ... as enum (...)` that introduced it, and any
    later `alter type ... add value`. Reading only the first would report a widened enum
    as still narrow.
    """
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))

    values: set[str] = set()

    created = re.search(
        rf"create\s+type\s+(?:public\.)?{name}\s+as\s+enum\s*\((.*?)\)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if created:
        values |= set(re.findall(r"'([^']+)'", created.group(1)))

    values |= set(
        re.findall(
            rf"alter\s+type\s+(?:public\.)?{name}\s+add\s+value\s+'([^']+)'",
            sql,
            re.IGNORECASE,
        )
    )

    return values


def test_the_migrations_are_readable():
    """A guard on the guard. If the parsing above silently found nothing, every test
    below would pass by comparing one empty set to another."""
    assert MIGRATIONS.is_dir()
    assert enum_values("offer_side") == {"sell", "buy"}


def test_every_import_status_exists_in_the_database():
    """The one that got us. 'failed' means an email arrived and nothing could be read
    out of it — the most useful row in the table on the day a supplier changes format,
    and the row Postgres was refusing."""
    from app.brain.reconcile import ImportStatus

    missing = set(get_args(ImportStatus)) - enum_values("import_status")
    assert not missing, (
        f"reconcile() can return {missing}, and public.import_status cannot store it"
    )


def test_every_classification_written_exists_in_the_database():
    from app.db.supabase_repo import _classification

    allowed = enum_values("email_classification")
    for side in ("sell", "buy", "unclassified", "", "anything else"):
        assert _classification(side) in allowed


def test_every_sender_method_exists_in_the_database():
    """`resolve_sender` sets this on every email, so an unknown one fails the write for
    every email that hits that path — not a subset."""
    from app.brain.sender import ResolutionMethod

    missing = set(get_args(ResolutionMethod)) - enum_values("sender_method")
    assert not missing, f"resolve_sender can return {missing}, absent from public.sender_method"


def test_every_offer_side_exists_in_the_database():
    from app.brain.classify import Side

    assert set(get_args(Side)) <= enum_values("offer_side")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("offer_status", {"live", "sold", "withdrawn", "expired"}),
        ("price_basis", {"per_unit", "per_lot", "unknown"}),
    ],
)
def test_enums_the_code_writes_literals_into(name, expected):
    """These are written as bare strings rather than through a type, so the only thing
    holding them together is this test."""
    assert expected <= enum_values(name)


# ---------------------------------------------------------------- enum values in use


def test_no_migration_uses_an_enum_value_it_just_added():
    """Postgres refuses to use a new enum value in the transaction that created it:

        ERROR: 55P04: unsafe use of new value "held_for_review" of enum type import_status
        HINT:  New enum values must be committed before they can be used.

    The Supabase SQL editor runs a script as one transaction, so this is not a
    theoretical rule — it is the error the migration fails with, halfway through, in
    front of whoever is deploying. It cost one deploy already: an ALTER TYPE ... ADD
    VALUE and a partial index whose predicate named that value, in one file.

    The fix is always the same shape — the ADD VALUE goes in its own migration, which
    commits, and the file that uses the value comes after it.
    """
    offenders = []

    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        added = set(re.findall(r"add value\s+'([^']+)'", text, re.IGNORECASE))
        if not added:
            continue

        # Everything after the last ADD VALUE in the file. A value used BEFORE it is
        # added is a different error and one the database would have caught anyway.
        tail = text[text.lower().rindex("add value"):]
        # Strip comments: the type's own comment quite properly names its values.
        body = "\n".join(
            line for line in tail.splitlines() if not line.lstrip().startswith("--")
        )

        for value in added:
            # In a predicate or a comparison, rather than merely mentioned.
            if re.search(rf"(?<!add value )'{re.escape(value)}'", body):
                offenders.append(f"{path.name} uses '{value}' after adding it")

    assert not offenders, (
        "a migration uses an enum value it adds in the same transaction:\n  "
        + "\n  ".join(offenders)
        + "\nPut the ADD VALUE in its own migration and use it in the next one."
    )
