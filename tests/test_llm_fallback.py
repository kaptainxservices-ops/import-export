"""The model as a fallback.

Almost every test here is about refusal, because that is where the value is. A model
that reads a Polish header correctly saves a table; a model that is believed when it is
wrong puts prices in the quantity column and quotes them to a customer.

Nothing in this file touches the network. `ask` is replaced with a function returning a
fixed string, which is exactly the seam the design exists to provide.
"""

from __future__ import annotations

import pytest

from app.brain.llm import columns as llm_columns
from app.brain.llm import side as llm_side
from app.brain.llm.client import Answer
from app.brain.tables import Grid, parse_grid

# A table whose header is in words the rules do not know: Polish, and abbreviated.
POLISH = Grid(
    rows=[
        ["Poz.", "Towar", "Szt.", "Kwota"],
        ["1", "iPhone 13 128GB Black", "12", "410,00"],
        ["2", "iPhone 13 256GB Blue", "6", "455,00"],
        ["3", "Samsung S23 256GB Green", "20", "389,50"],
        ["4", "Samsung A54 128GB Black", "44", "188,00"],
        ["5", "Xiaomi Redmi Note 12 128GB", "31", "121,00"],
    ],
    source="lista.xlsx",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    llm_columns.clear_cache()
    yield
    llm_columns.clear_cache()


def _replies(text: str, *, model: str = "claude-sonnet-5"):
    """Stand in for `ask`, counting how many times it was called."""
    calls: list[tuple[str, str]] = []

    def fake(system: str, user: str, **kwargs):
        calls.append((system, user))
        return Answer(text=text, model=model, input_tokens=900, output_tokens=20)

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def _mapper(monkeypatch, reply: str):
    monkeypatch.setattr(llm_columns, "available", lambda: True)
    fake = _replies(reply)
    monkeypatch.setattr(llm_columns, "ask", fake)
    return fake


# ---------------------------------------------------------------- no key


def test_without_a_key_nothing_is_asked():
    """The whole package is inert until a key exists. `mapper_for` returning None means
    parse_grid takes the code path it took before this package was written."""
    from app.brain.llm import UsageLog, available, mapper_for

    assert available() is False
    assert mapper_for(UsageLog()) is None
    assert llm_side.classify_side_llm("Stock list", "iPhone 13 @ 410") is None


def test_a_readable_table_never_reaches_the_model(monkeypatch):
    """The rules run first, always. Paying a model to re-read a header it already
    understood would be a bill that scales with the client's inbox."""
    fake = _mapper(monkeypatch, '{"description": 1, "price": 3}')

    grid = Grid(rows=[["EAN", "Description", "Qty", "Price"], ["", "iPhone 13", "5", "410"]])
    table = parse_grid(grid, column_mapper=llm_columns.map_columns_llm)

    assert table.rows
    assert fake.calls == []


# ---------------------------------------------------------------- one call per table


def test_the_model_is_asked_once_for_a_whole_table(monkeypatch):
    """Once per table, not once per row. That is the difference between a few hundred
    tokens for a supplier's morning list and several hundred thousand."""
    fake = _mapper(monkeypatch, '{"description": 1, "quantity": 2, "price": 3}')

    extra = [[str(n), f"iPhone {n} 128GB", "5", "300,00"] for n in range(200)]
    big = Grid(rows=POLISH.rows + extra)
    table = parse_grid(big, decimal_hint="comma", column_mapper=llm_columns.map_columns_llm)

    assert len(fake.calls) == 1
    assert table.row_count > 190


def test_the_same_header_tomorrow_is_free(monkeypatch):
    """Suppliers send the same header every morning for years."""
    fake = _mapper(monkeypatch, '{"description": 1, "price": 3}')

    for _ in range(5):
        parse_grid(POLISH, decimal_hint="comma", column_mapper=llm_columns.map_columns_llm)

    assert len(fake.calls) == 1


def test_a_failed_call_is_not_cached(monkeypatch):
    """A rate limit says nothing about the header. Caching the failure would blind this
    supplier's table for the life of the process."""
    monkeypatch.setattr(llm_columns, "available", lambda: True)
    monkeypatch.setattr(llm_columns, "ask", lambda *a, **k: None)

    assert llm_columns.map_columns_llm(POLISH.rows[0], POLISH.rows[1:]) is None
    assert llm_columns._signature(POLISH.rows[0], 4) not in llm_columns._cache


# ---------------------------------------------------------------- the rescue


def test_the_model_can_rescue_an_unreadable_header(monkeypatch):
    """The happy path — and note where the numbers come from.

    The model returned three integers. Every price and every quantity below was read out
    of the sheet by the deterministic parser; the model only said which column to read.
    """
    _mapper(monkeypatch, '{"description": 1, "quantity": 2, "price": 3}')

    table = parse_grid(POLISH, decimal_hint="comma", column_mapper=llm_columns.map_columns_llm)

    assert table.needs_column_mapping is False
    assert table.row_count == 5
    assert [row.price for row in table.rows] == [410.0, 455.0, 389.5, 188.0, 121.0]
    assert [row.quantity for row in table.rows] == [12, 6, 20, 44, 31]
    assert table.rows[0].description == "iPhone 13 128GB Black"


# ---------------------------------------------------------------- refusals


def test_a_column_that_is_not_prices_is_refused(monkeypatch):
    """The model proposes; the sample rows decide.

    Here it points 'price' at the position number — 1, 2, 3, 4, 5. Those parse as money
    perfectly well, which is exactly why the check is proportional rather than binary:
    the description column is the one that gives it away, and both must hold.
    """
    _mapper(monkeypatch, '{"description": 2, "price": 0}')

    table = parse_grid(POLISH, decimal_hint="comma", column_mapper=llm_columns.map_columns_llm)
    assert table.needs_column_mapping is True
    assert table.rows == []


def test_true_is_not_a_column_index(monkeypatch):
    """`isinstance(True, int)` is True in Python, so an unguarded mapping would read
    every price out of column 1."""
    _mapper(monkeypatch, '{"description": 1, "price": true}')

    table = parse_grid(POLISH, column_mapper=llm_columns.map_columns_llm)
    assert table.needs_column_mapping is True


def test_an_index_off_the_end_is_refused(monkeypatch):
    _mapper(monkeypatch, '{"description": 1, "price": 9}')

    table = parse_grid(POLISH, column_mapper=llm_columns.map_columns_llm)
    assert table.needs_column_mapping is True


def test_two_fields_cannot_claim_one_column(monkeypatch):
    """Description and price in the same column is not a table, it is a contradiction."""
    _mapper(monkeypatch, '{"description": 1, "price": 1}')

    table = parse_grid(POLISH, column_mapper=llm_columns.map_columns_llm)
    assert table.needs_column_mapping is True


def test_an_unknown_field_name_is_dropped(monkeypatch):
    """Only the nine known fields survive, so nothing new can appear on a row."""
    _mapper(monkeypatch, '{"description": 1, "price": 3, "supplier_margin": 2}')

    mapping = llm_columns.map_columns_llm(POLISH.rows[0], POLISH.rows[1:])
    assert mapping is not None
    assert set(mapping.columns) == {"description", "price"}


def test_prose_instead_of_json_is_refused(monkeypatch):
    _mapper(monkeypatch, "I think column 1 is the description.")

    table = parse_grid(POLISH, column_mapper=llm_columns.map_columns_llm)
    assert table.needs_column_mapping is True


def test_an_optional_field_is_dropped_rather_than_failing_the_table(monkeypatch):
    """Losing a quantity leaves a row that still says what is for sale and what it costs.
    A wrong quantity promises stock nobody has, so the mapping is trimmed, not thrown."""
    _mapper(monkeypatch, '{"description": 1, "price": 3, "ean": 0}')

    table = parse_grid(POLISH, decimal_hint="comma", column_mapper=llm_columns.map_columns_llm)

    assert table.row_count == 5
    assert table.column_map is not None
    assert "ean" not in table.column_map.columns
    assert all(row.ean is None for row in table.rows)


def test_a_signature_block_is_not_worth_a_token(monkeypatch):
    """Marketing emails are built out of tables — layout scaffolding, contact details,
    social icons. 108 of the 143 tables in the sample had an unreadable header and most
    were that. A table with no column of numbers has no price and no quantity, so no
    mapping of it could be usable, and asking is a charge with no answer available."""
    fake = _mapper(monkeypatch, '{"description": 0, "price": 1}')

    contacts = Grid(
        rows=[
            ["Ömer Yiğit", "Product Manager", "yigit@hitisy.de", "German, English"],
            ["Murat Mert", "Deputy Managing Director", "murat@hitisy.de", "German"],
            ["Sales", "Key Account", "sales@hitisy.de", "English"],
            ["Support", "Logistics", "ops@hitisy.de", "Turkish"],
            ["Accounts", "Finance", "fin@hitisy.de", "German"],
        ]
    )
    table = parse_grid(contacts, column_mapper=llm_columns.map_columns_llm)

    assert table.needs_column_mapping is True
    assert fake.calls == []


def test_a_two_row_scrap_is_not_worth_a_token(monkeypatch):
    fake = _mapper(monkeypatch, '{"description": 0, "price": 1}')

    scrap = Grid(rows=[["Logo", ""], ["Follow us", "50"]])
    parse_grid(scrap, column_mapper=llm_columns.map_columns_llm)

    assert fake.calls == []


def test_a_mapper_that_raises_does_not_take_the_email_down(monkeypatch):
    """The table was already unreadable. An exception here would lose the rest of the
    email along with it."""

    def explode(header, sample):
        raise RuntimeError("connection reset")

    table = parse_grid(POLISH, column_mapper=explode)
    assert table.needs_column_mapping is True


# ---------------------------------------------------------------- classification


def _says(monkeypatch, word: str):
    monkeypatch.setattr(llm_side, "available", lambda: True)
    monkeypatch.setattr(
        llm_side,
        "ask",
        lambda *a, **k: Answer(text=word, model="claude-haiku-4-5-20251001",
                               input_tokens=700, output_tokens=1),
    )


def test_the_answer_must_be_one_of_three_words(monkeypatch):
    """A model that will not follow an instruction this small is not a model to take a
    filing decision from."""
    _says(monkeypatch, "definitely a sell, I'd say!")

    verdict = llm_side.classify_side_llm("Fwd: whatsapp", "iPhone 13 128 @ 410")
    assert verdict is not None
    assert verdict.side is None


@pytest.mark.parametrize("word,expected", [("sell", "sell"), ("buy", "buy"), ("unclear", None)])
def test_the_three_permitted_answers(monkeypatch, word, expected):
    _says(monkeypatch, word)
    verdict = llm_side.classify_side_llm("Fwd: whatsapp", "iPhone 13 128 @ 410")
    assert verdict is not None and verdict.side == expected


def test_a_trailing_full_stop_is_tolerated(monkeypatch):
    _says(monkeypatch, "Sell.")
    verdict = llm_side.classify_side_llm("Fwd: whatsapp", "iPhone 13 128 @ 410")
    assert verdict is not None and verdict.side == "sell"


def test_an_empty_email_is_not_worth_asking_about(monkeypatch):
    _says(monkeypatch, "sell")
    assert llm_side.classify_side_llm("", "") is None


# ---------------------------------------------------------------- in the pipeline

from datetime import datetime  # noqa: E402

from app.brain.llm.side import SideVerdict  # noqa: E402
from app.db.memory import InMemoryRepository  # noqa: E402
from app.db.models import TenantConfig  # noqa: E402
from app.pipeline import process_email  # noqa: E402
from app.schemas.email import Attachment, InboundEmail  # noqa: E402

TENANT = TenantConfig(id="tenant-1", name="TVD")
ROWS = [
    ["Description", "Qty", "Price"],
    ["Apple iPhone 15 128GB Black", "50", "579"],
    ["Apple iPhone 16 128GB Teal", "45", "679"],
]


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT])


def _email(subject: str, body: str = "Sent from my iPhone", message_id: str = "<m1@x>"):
    return InboundEmail(
        tenant_id="tenant-1",
        message_id=message_id,
        from_email="sales@supplier.example",
        subject=subject,
        received_at=datetime(2026, 8, 7, 9, 0),
        body_text=body,
        attachments=[Attachment(filename="stock.xlsx", kind="table", rows=ROWS)],
    )


def _verdict(monkeypatch, side, tokens=(700, 1)):
    import app.pipeline as pipeline

    answer = Answer(
        text=side or "unclear",
        model="claude-haiku-4-5-20251001",
        input_tokens=tokens[0],
        output_tokens=tokens[1],
    )
    monkeypatch.setattr(pipeline, "classify_side_llm", lambda *a: SideVerdict(side, answer))


def test_the_model_rescues_an_email_the_rules_could_not_read(repo, monkeypatch):
    """A forwarded WhatsApp offer: no WTS, no WTB, no price list, nothing to match on.
    Eleven of the 45 sample emails look like this."""
    _verdict(monkeypatch, "sell")

    result = process_email(_email("Fwd: message"), repo)

    assert result.outcome == "processed"
    assert result.side == "sell"
    assert result.model_calls == 1


def test_a_disagreement_refuses_rather_than_guessing(repo, monkeypatch):
    """The rules leaned one way weakly and the model leans the other. Two guesses and no
    reason to prefer either — so it goes to a person, which costs one click. Filing it
    on a coin toss puts a supplier's catalogue on the board as customer demand."""
    _verdict(monkeypatch, "buy")

    # 'stock offer' in the body, nothing in the subject: a weak sell.
    result = process_email(_email("Re: yesterday", "our stock offer follows"), repo)

    assert result.outcome == "unclassified"
    assert "neither is confident" in result.notes[0]


def test_agreement_raises_confidence_and_files_it(repo, monkeypatch):
    _verdict(monkeypatch, "sell")

    result = process_email(_email("Re: yesterday", "our stock offer follows"), repo)

    assert result.outcome == "processed"
    assert result.side == "sell"


def test_a_confident_rule_is_never_second_guessed(repo, monkeypatch):
    """'WTS Price List' in the subject is unambiguous. Sending it to a model would be a
    bill that scales with the inbox and buys nothing."""
    import app.pipeline as pipeline

    def refuse(*args):
        raise AssertionError("the model was asked about an email the rules had settled")

    monkeypatch.setattr(pipeline, "classify_side_llm", refuse)

    assert process_email(_email("WTS Price List 07.08"), repo).side == "sell"


def test_a_model_outage_leaves_the_deterministic_answer(repo, monkeypatch):
    """Rate limit, revoked key, timeout — the email lands exactly where it would have
    landed before the key existed."""
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "classify_side_llm", lambda *a: None)

    result = process_email(_email("Fwd: message"), repo)

    assert result.outcome == "unclassified"
    assert result.model_calls == 0


def test_what_the_model_cost_is_recorded_against_the_tenant(repo, monkeypatch):
    """Anthropic is the only cost that scales linearly with clients, so 'is this client
    profitable?' should be a query rather than a guess."""
    _verdict(monkeypatch, "sell", tokens=(1000, 2))

    process_email(_email("Fwd: message"), repo)

    assert len(repo.usage) == 1
    event = repo.usage[0]
    assert event.tenant_id == "tenant-1"
    assert event.email_id is not None
    assert event.operation == "classify"
    assert (event.input_tokens, event.output_tokens) == (1000, 2)
    assert event.cost_usd == pytest.approx(0.00101)


def test_usage_is_recorded_even_when_the_email_is_then_refused(repo, monkeypatch):
    """A call that has been paid for is a call that gets recorded, whatever the pipeline
    decides afterwards."""
    _verdict(monkeypatch, None)

    result = process_email(_email("Fwd: message"), repo)

    assert result.outcome == "unclassified"
    assert len(repo.usage) == 1
