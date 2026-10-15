"""The identity key, as Python computes it and as Postgres computes it.

`identity_key` exists twice: once in `ProductSpec.identity_key()` and once as a stored
generated column. They are not derived from each other, so nothing stops one from being
edited alone — and when that happens reconciliation does not fail, it quietly stops
recognising yesterday's rows and starts writing duplicates beside them. Every comment in
both files says the two must stay in step; this is the thing that checks.

The SQL is read from the migration and evaluated here, rather than being copied into a
Python function and compared against itself. A copy would agree with Python for ever,
including the day the migration changes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.brain.normalise import parse_product

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


# ---------------------------------------------------------------- reading the SQL


def generated_expression() -> str:
    """The `case ... end` body of the newest `identity_key` generated column."""
    pattern = re.compile(
        r"add column if not exists identity_key text generated always as\s*\((.*?)\)\s*stored",
        re.IGNORECASE | re.DOTALL,
    )

    found = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            found = match.group(1).strip()

    assert found, "no identity_key generated column found in any migration"
    return found


# ---------------------------------------------------------------- evaluating it

_TOKEN = re.compile(
    r"""
      (?P<string>'(?:[^']|'')*')
    | (?P<concat>\|\|)
    | (?P<kw>\b(?:case|when|then|else|end|is|not|null|and|or)\b)
    | (?P<func>\b(?:lower|coalesce)\b)
    | (?P<cast>::\s*text)
    | (?P<op><>|=)
    | (?P<name>[a-z_][a-z0-9_]*)
    | (?P<punct>[(),])
    """,
    re.IGNORECASE | re.VERBOSE,
)


def tokenise(sql: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    position = 0
    for match in _TOKEN.finditer(sql):
        gap = sql[position : match.start()]
        assert not gap.strip(), f"unhandled SQL near {gap.strip()!r} — teach the evaluator"
        position = match.end()
        kind = match.lastgroup
        assert kind
        out.append((kind, match.group()))
    assert not sql[position:].strip(), f"unhandled SQL tail {sql[position:].strip()!r}"
    return out


class Evaluator:
    """Enough SQL to read this one expression, and no more.

    Deliberately small: `case`, `||`, `lower`, `coalesce`, `::text`, `is not null` and
    `<> ''`. Anything else raises rather than being guessed at, so a migration that
    starts using a construct this does not model fails the test instead of passing it
    on a wrong reading.
    """

    def __init__(self, tokens: list[tuple[str, str]], row: dict[str, object]):
        self.tokens = tokens
        self.row = row
        self.i = 0

    def peek(self) -> str | None:
        return self.tokens[self.i][1].lower() if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def expect(self, word: str) -> None:
        kind, value = self.take()
        assert value.lower() == word, f"expected {word!r}, found {value!r}"

    # expression := term (|| term)*
    def expression(self) -> object:
        value = self.term()
        while self.peek() == "||":
            self.take()
            right = self.term()
            # Concatenation with NULL yields NULL, as in Postgres. It matters here
            # rather than being a detail: both arms of the `case` are parsed, so the
            # arm that was not taken is evaluated too, and with a null EAN that arm is
            # exactly `'ean:' || null`. Raising on it, or treating null as an empty
            # string, would both give the wrong answer for every spec row.
            value = None if value is None or right is None else f"{value}{right}"
        return value

    def term(self) -> object:
        kind, value = self.take()

        if kind == "string":
            return value[1:-1].replace("''", "'")

        if kind == "kw" and value.lower() == "case":
            return self.case()

        if kind == "func":
            name = value.lower()
            self.expect("(")
            args = [self.expression()]
            while self.peek() == ",":
                self.take()
                args.append(self.expression())
            self.expect(")")
            if name == "lower":
                return None if args[0] is None else str(args[0]).lower()
            return next((a for a in args if a is not None), None)

        if kind == "name":
            assert value in self.row, f"column {value!r} is not in the row"
            result = self.row[value]
            if self.peek() == "::text":
                self.take()
                result = None if result is None else str(result)
            return result

        if kind == "punct" and value == "(":
            inner = self.expression()
            self.expect(")")
            return inner

        raise AssertionError(f"unexpected token {value!r}")

    def case(self) -> object:
        """`case when <condition> then <expr> else <expr> end` — no CASE value form."""
        result = None
        chosen = False
        while True:
            word = self.peek()
            if word == "when":
                self.take()
                condition = self.condition()
                self.expect("then")
                value = self.expression()
                if condition and not chosen:
                    result, chosen = value, True
            elif word == "else":
                self.take()
                value = self.expression()
                if not chosen:
                    result, chosen = value, True
            elif word == "end":
                self.take()
                return result
            else:
                raise AssertionError(f"unexpected token in case: {word!r}")

    def condition(self) -> bool:
        value = self.comparison()
        while self.peek() in {"and", "or"}:
            _, word = self.take()
            right = self.comparison()
            value = (value and right) if word.lower() == "and" else (value or right)
        return value

    def comparison(self) -> bool:
        left = self.term()

        if self.peek() == "is":
            self.take()
            negated = self.peek() == "not"
            if negated:
                self.take()
            self.expect("null")
            return (left is not None) if negated else (left is None)

        kind, operator = self.take()
        assert kind == "op", f"expected a comparison, found {operator!r}"
        right = self.term()
        return left != right if operator == "<>" else left == right


def sql_identity_key(spec) -> str:
    """What Postgres would store for a row carrying this spec's fields."""
    row = {
        "ean": spec.ean,
        "brand": spec.brand,
        "description_key": spec.description_key,
        "capacity_gb": spec.capacity_gb,
        "colour": spec.colour,
        "edition": spec.edition,
        "part_number": spec.part_number,
    }
    evaluator = Evaluator(tokenise(generated_expression()), row)
    value = evaluator.expression()
    assert evaluator.i == len(evaluator.tokens), "trailing SQL left unread"
    return str(value)


# ---------------------------------------------------------------- the agreement

LINES = [
    # EAN present: the branch this migration changed.
    ("Apple iPhone 17 Pro Max 256GB Cosmic Orange", "0195950643701"),
    ("APPLE IPHONE 17 256GB WHITE", "0195950643701"),
    ('Samsung Galaxy Tab (X406) S10 Lite 10.9" 128GB/Gray WiFi', "8806097728955"),
    ('Samsung Galaxy Tab (X406) S10 Lite 10.9" 128GB/Silver WiFi', "8806097728955"),
    ("Samsung A56 5G Ent. Ed. 8+128 Graphite", "8806095558792"),
    ("Samsung A56 5G OM 8+128 Graphite", "8806095558792"),
    ("USB-C to Lightning cable 2m", "4047443459848"),
    ("Apple iPhone Air 1TGB Sky Blue", "0195950643718"),
    # No EAN: the spec fallback.
    ("Apple iPhone 14 128GB White", None),
    ("Motorola Edge 70 Pro 5G 8/256GB PANTONE Titan", None),
    ("Echo Spot 2024 speaker", None),
    ("Samsung A37 5G DS A376 256GB Ent. Ed.", None),
    ("Nokia 3210 4G DS", None),
    # Nothing to key on at all.
    ("", None),
    ("   ", None),
]


@pytest.mark.parametrize(("line", "ean"), LINES)
def test_python_and_postgres_build_the_same_key(line, ean):
    spec = parse_product(line, ean=ean)
    assert spec.identity_key() == sql_identity_key(spec)


def test_the_evaluator_is_actually_reading_the_migration():
    """A tautology guard. If `generated_expression` quietly returned something inert,
    every test above would pass while checking nothing."""
    expression = generated_expression()

    assert "ean" in expression
    assert "description_key" in expression
    for column in ("capacity_gb", "colour", "edition", "part_number"):
        assert column in expression, f"{column} is part of identity in Python but not in SQL"


def test_a_drifted_migration_is_caught():
    """The failure this file exists for: SQL edited, Python not."""
    spec = parse_product("Apple iPhone 17 256GB White", ean="0195950643701")
    drifted = "case when ean is not null and ean <> '' then 'ean:' || ean else 'spec:' end"

    row = {
        "ean": spec.ean, "brand": spec.brand, "description_key": spec.description_key,
        "capacity_gb": spec.capacity_gb, "colour": spec.colour, "edition": spec.edition,
        "part_number": spec.part_number,
    }
    key = Evaluator(tokenise(drifted), row).expression()

    assert key == "ean:0195950643701"
    assert key != spec.identity_key()
