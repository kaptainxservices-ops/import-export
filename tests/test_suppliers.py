"""Per-supplier parsing settings.

These two fields are the difference between €1,079 and €1.07, and between USD and AED on
a bare '$'. A currency error does not present as an error — it presents as an unusually
good margin — so the tests here are mostly about refusing bad input rather than accepting
good input.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.suppliers import SupplierPatch
from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.dependencies import set_repository

TENANT = TenantConfig(id="tenant-1", name="TVD")


@pytest.fixture
def repo():
    store = InMemoryRepository([TENANT])
    store.create_counterparty("tenant-1", "sales@automic.example", "Automic")
    return store


# ---------------------------------------------------------------- what may change


def test_only_the_parsing_settings_can_be_written(repo):
    """A whitelist rather than 'whatever was sent'. This is a PATCH straight into a
    table, and without it `tenant_id` would be settable — which moves a supplier onto
    another client's board."""
    supplier = next(iter(repo.counterparties.values()))

    repo.update_supplier(
        "tenant-1",
        supplier.id,
        {"decimal_separator": "comma", "tenant_id": "tenant-2", "id": "hijacked"},
    )

    updated = repo.counterparties[supplier.id]
    assert updated.decimal_separator == "comma"
    assert updated.id == supplier.id


def test_a_supplier_on_another_board_cannot_be_changed(repo):
    assert repo.update_supplier("tenant-1", "not-a-supplier", {"default_currency": "EUR"}) is False


def test_nothing_settable_means_no_write(repo):
    supplier = next(iter(repo.counterparties.values()))
    assert repo.update_supplier("tenant-1", supplier.id, {"country": "PL"}) is False


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize("value", ["comma", "dot"])
def test_the_two_number_formats(value):
    assert SupplierPatch(decimal_separator=value).decimal_separator == value


@pytest.mark.parametrize("value", ["period", ".", ",", "european", "COMMA"])
def test_anything_else_is_not_a_number_format(value):
    with pytest.raises(ValidationError):
        SupplierPatch(decimal_separator=value)


def test_the_currency_must_be_one_we_recognise():
    assert SupplierPatch(default_currency="AED").default_currency == "AED"
    with pytest.raises(ValidationError):
        SupplierPatch(default_currency="EURO")
    with pytest.raises(ValidationError):
        SupplierPatch(default_currency="XYZ")


def test_staleness_is_held_to_what_the_column_accepts():
    """The column is `between 1 and 720`. Checked here so the message says what is wrong
    instead of arriving as a Postgres constraint name."""
    assert SupplierPatch(staleness_hours=24).staleness_hours == 24
    with pytest.raises(ValidationError):
        SupplierPatch(staleness_hours=0)
    with pytest.raises(ValidationError):
        SupplierPatch(staleness_hours=1000)


def test_clearing_a_field_differs_from_not_mentioning_it():
    """An empty string means 'unset it'. Not sending the field means 'leave it'. Reading
    both as the same thing would wipe a setting every time an unrelated one changed."""
    assert SupplierPatch(default_currency="").changes() == {"default_currency": None}
    assert SupplierPatch(decimal_separator="dot").changes() == {"decimal_separator": "dot"}
    assert SupplierPatch().changes() == {}


# ---------------------------------------------------------------- the settings bite


def test_the_separator_decides_what_a_price_means():
    """Why any of this exists. The same five characters, two suppliers, a factor of a
    thousand between them — and both conventions appear in this inbox in the same week."""
    from app.brain.normalise import parse_price

    assert parse_price("1.079", decimal_hint="comma") == 1079.00
    assert parse_price("1.079", decimal_hint="dot") == 1.079

    assert parse_price("109,50", decimal_hint="comma") == 109.50
    assert parse_price("12,500", decimal_hint="dot") == 12500.00


def test_the_summary_carries_the_evidence_for_the_settings(repo):
    """The list shows each supplier's own recent prices. 'Does this one write commas or
    dots?' is unanswerable in the abstract and obvious with five of their numbers in
    front of you, and a screen that asks a question it gives no way to answer gets
    filled in wrongly."""
    summaries = repo.list_suppliers("tenant-1")
    assert len(summaries) == 1
    assert summaries[0].primary_email == "sales@automic.example"
    assert summaries[0].sample_prices == []


# ---------------------------------------------------------------- the endpoints


def test_listing_requires_a_token(client):
    set_repository(InMemoryRepository([TENANT]))
    try:
        assert client.get("/suppliers").status_code == 401
    finally:
        set_repository(None)


def test_patching_requires_a_token(client):
    set_repository(InMemoryRepository([TENANT]))
    try:
        response = client.patch("/suppliers/x", json={"default_currency": "EUR"})
        assert response.status_code == 401
    finally:
        set_repository(None)


def test_authentication_is_checked_before_the_body(client):
    assert client.patch("/suppliers/x", json={"default_currency": "NONSENSE"}).status_code == 401


def test_the_tenant_cannot_be_passed_in(client):
    import inspect

    from app.api.suppliers import SupplierPatch as Patch
    from app.api.suppliers import list_suppliers, update_supplier

    for endpoint in (list_suppliers, update_supplier):
        assert "tenant_id" not in inspect.signature(endpoint).parameters
    assert "tenant_id" not in Patch.model_fields
