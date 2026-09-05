"""Per-supplier parsing settings — the two facts the parser cannot work out for itself.

`decimal_separator` and `default_currency` are not preferences. They decide whether
Automic's '1.079' is a thousand euros or one, and whether Masterfone's bare '$' is USD
or AED. Neither can be settled from the text: both conventions appear in the same inbox
in the same week, and a currency error does not present as an error. It presents as an
unusually good margin, which is the kind of number a broker acts on before checking.

So they are configuration, set once per supplier by somebody who knows them. Until this
existed they were columns with no way to reach them.

The list comes back with each supplier's actual recent prices attached, because the
question "does this one write commas or dots?" is unanswerable in the abstract and
obvious with five of their own numbers in front of you.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.api.deps import current_tenant

router = APIRouter(prefix="/suppliers", tags=["suppliers"])
log = logging.getLogger(__name__)

CURRENCIES = (
    "EUR", "USD", "GBP", "AED", "PLN", "CHF", "CZK", "SEK",
    "HKD", "SGD", "INR", "CNY", "TRY", "RON",
)


class SupplierOut(BaseModel):
    id: str
    primary_email: str
    name: str | None
    country: str | None
    default_currency: str | None
    decimal_separator: str | None
    staleness_hours: int | None
    typical_row_count: int | None
    live_offers: int
    last_seen_at: str | None
    sample_prices: list[str]


class SuppliersOut(BaseModel):
    items: list[SupplierOut]
    unconfigured: int = Field(
        description="How many have neither a currency nor a separator set. These are the "
        "ones whose prices are being read on a guess."
    )
    currencies: list[str]


class SupplierPatch(BaseModel):
    name: str | None = None
    default_currency: str | None = None
    decimal_separator: str | None = None
    staleness_hours: int | None = None
    typical_row_count: int | None = None

    @model_validator(mode="after")
    def _sane(self):
        if self.decimal_separator not in (None, "", "comma", "dot"):
            raise ValueError("decimal_separator must be 'comma' or 'dot'")

        if self.default_currency not in (None, "") and self.default_currency not in CURRENCIES:
            raise ValueError(f"default_currency must be one of {', '.join(CURRENCIES)}")

        # The column is `between 1 and 720`. Caught here so the message says what is
        # wrong rather than arriving as a Postgres constraint name.
        if self.staleness_hours is not None and not 1 <= self.staleness_hours <= 720:
            raise ValueError("staleness_hours must be between 1 and 720")

        if self.typical_row_count is not None and self.typical_row_count < 0:
            raise ValueError("typical_row_count cannot be negative")

        return self

    def changes(self) -> dict:
        """Only the fields actually sent. An empty string means 'clear it', which is not
        the same as not mentioning the field, and both differ from a real value."""
        out: dict = {}
        for field_name in self.model_fields_set:
            value = getattr(self, field_name)
            out[field_name] = None if value == "" else value
        return out


@router.get("", response_model=SuppliersOut, summary="Suppliers and their parsing settings")
def list_suppliers(context=Depends(current_tenant)) -> SuppliersOut:
    tenant_id, repository = context
    items = repository.list_suppliers(tenant_id)

    return SuppliersOut(
        items=[
            SupplierOut(
                id=s.id,
                primary_email=s.primary_email,
                name=s.name,
                country=s.country,
                default_currency=s.default_currency,
                decimal_separator=s.decimal_separator,
                staleness_hours=s.staleness_hours,
                typical_row_count=s.typical_row_count,
                live_offers=s.live_offers,
                last_seen_at=s.last_seen_at.isoformat() if s.last_seen_at else None,
                sample_prices=s.sample_prices,
            )
            for s in items
        ],
        unconfigured=sum(
            1 for s in items if not s.default_currency and not s.decimal_separator
        ),
        currencies=list(CURRENCIES),
    )


@router.patch("/{supplier_id}", response_model=SupplierOut, summary="Change one supplier")
def update_supplier(
    supplier_id: str, patch: SupplierPatch, context=Depends(current_tenant)
) -> SupplierOut:
    tenant_id, repository = context

    changes = patch.changes()
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nothing to change")

    if not repository.update_supplier(tenant_id, supplier_id, changes):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such supplier on this board")

    log.info("supplier %s updated on tenant %s: %s", supplier_id, tenant_id, sorted(changes))

    updated = next(
        (s for s in repository.list_suppliers(tenant_id) if s.id == supplier_id), None
    )
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such supplier on this board")

    return SupplierOut(
        id=updated.id,
        primary_email=updated.primary_email,
        name=updated.name,
        country=updated.country,
        default_currency=updated.default_currency,
        decimal_separator=updated.decimal_separator,
        staleness_hours=updated.staleness_hours,
        typical_row_count=updated.typical_row_count,
        live_offers=updated.live_offers,
        last_seen_at=updated.last_seen_at.isoformat() if updated.last_seen_at else None,
        sample_prices=updated.sample_prices,
    )
