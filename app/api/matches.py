"""Matching, exposed to the dashboard.

The matching engine is Python, and the dashboard is a browser. Rather than reimplement
the logic in TypeScript — two copies of the rule that decides what to promise a buyer,
drifting apart — the dashboard calls this.

**The tenant is never taken from the request.** The browser sends only a Supabase
session token; this resolves the user from it and then looks their tenant up in
`profiles`. A tenant id in a query string is a request to read someone else's board,
and the fact that the dashboard would never send one is not a defence.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import current_tenant
from app.brain.matching import (
    MatchOption,
    Requirement,
    Supply,
    fill_requirement,
    group_options,
    place_offer,
)
from app.brain.normalise import expand_variants
from app.brain.normalise.product import build_match_key
from app.db.models import BoardOffer

router = APIRouter(prefix="/matches", tags=["matches"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------- responses


class AllocationOut(BaseModel):
    offer_id: str
    counterparty_id: str
    counterparty_name: str | None = None
    quantity: int
    unit_price: float
    currency: str | None = None
    description: str


class OptionOut(BaseModel):
    kind: str
    allocations: list[AllocationOut]
    requested_quantity: int | None
    filled_quantity: int
    shortfall: int
    supplier_count: int
    blended_unit_cost: float | None
    counterpart_unit_price: float | None
    unit_margin: float | None
    total_margin: float | None
    margin_pct: float | None
    relaxed: list[str]
    warnings: list[str]


class MatchesOut(BaseModel):
    subject_id: str
    subject_description: str
    subject_quantity: int | None
    subject_price: float | None
    subject_currency: str | None
    direction: str
    groups: dict[str, list[OptionOut]]
    total_options: int


# ---------------------------------------------------------------- auth


class BoardRowOut(BaseModel):
    offer_id: str
    description: str
    spec: str
    counterparty_name: str | None
    country: str | None
    quantity: int | None
    unit_price: float | None
    currency: str | None
    last_confirmed_at: str | None

    state: str = Field(description="'filled' | 'short' | 'near_miss' | 'no_supply'")
    option_count: int
    near_miss_count: int
    best_summary: str | None = None
    blended_unit_cost: float | None = None
    total_margin: float | None = None
    margin_pct: float | None = None
    shortfall: int = 0
    filled_quantity: int = 0
    note: str | None = None


class BoardOut(BaseModel):
    rows: list[BoardRowOut]
    live_matches: int
    unmet_demand: int
    opportunity: float
    currency: str | None
    supply_rows: int


# ---------------------------------------------------------------- endpoints


@router.get("/board", response_model=BoardOut, summary="Every buyer requirement, matched")
def board(
    limit: int = Query(300, ge=1, le=1000),
    context=Depends(current_tenant),
) -> BoardOut:
    """Run the matching engine across the whole board, not one row at a time.

    The drawer answers "can I fill *this*?". This answers the question a trader actually
    starts the morning with — "where is there money today?" — and it is a different
    screen because the answer has to be ranked by margin across every requirement at
    once rather than discovered by clicking.

    Computed per request rather than cached. It takes well under a second over the full
    sample board, and a cached number here would be a stale promise: supply changes every
    time a supplier's list lands, which is the whole point of the system.
    """
    tenant_id, repository = context

    demand = repository.load_board(tenant_id, "buy")
    supply_offers = repository.load_board(tenant_id, "sell")
    supply = [_supply(o) for o in supply_offers]

    # Free: every supply row already carries its counterparty's name from the join, so
    # the allocation summary can say 'Al Manar 40' instead of a uuid without a second
    # query.
    names = {o.counterparty_id: o.counterparty_name for o in supply_offers}

    rows: list[BoardRowOut] = []
    opportunity = 0.0
    filled = 0
    unmet = 0
    currency: str | None = None

    for offer in demand[:limit]:
        options = fill_requirement(_requirement(offer), supply)

        # A near miss relaxed something — a different capacity, another colour. It is not
        # a fill, and counting it as one would put money in the total that nobody can
        # actually collect.
        real = [o for o in options if o.kind != "near_miss"]
        near = [o for o in options if o.kind == "near_miss"]

        best = real[0] if real else None
        if best is not None and best.total_margin:
            opportunity += best.total_margin
            currency = currency or offer.currency

        if best is None:
            state = "near_miss" if near else "no_supply"
            unmet += 1
        elif best.shortfall:
            state = "short"
            filled += 1
        else:
            state = "filled"
            filled += 1

        rows.append(
            BoardRowOut(
                offer_id=offer.id,
                description=offer.description,
                spec=" · ".join(
                    p for p in (
                        f"{offer.capacity_gb}GB" if offer.capacity_gb else None,
                        offer.colour,
                    ) if p
                ),
                counterparty_name=offer.counterparty_name,
                country=offer.country,
                quantity=offer.quantity,
                unit_price=offer.unit_price,
                currency=offer.currency,
                last_confirmed_at=None,
                state=state,
                option_count=len(real),
                near_miss_count=len(near),
                best_summary=_summarise(best, names) if best else None,
                blended_unit_cost=best.blended_unit_cost if best else None,
                total_margin=best.total_margin if best else None,
                margin_pct=best.margin_pct if best else None,
                shortfall=best.shortfall if best else 0,
                filled_quantity=best.filled_quantity if best else 0,
                note=_note(best, near),
            )
        )

    # Ranked by whether it can be filled first, and only then by money.
    #
    # Sorting on margin alone was wrong for this trade, and the sample board shows why:
    # a WTB list states what a buyer wants and almost never what they will pay, so 26 of
    # 26 fillable requirements have an unknown margin. Ranking those below a dead row
    # buries every actionable match under demand nobody can serve.
    #
    # Within a state, a known margin beats an unknown one, and an unknown one beats a
    # smaller fill.
    order = {"filled": 0, "short": 1, "near_miss": 2, "no_supply": 3}
    rows.sort(
        key=lambda r: (
            order.get(r.state, 9),
            -(r.total_margin or 0),
            -(r.filled_quantity or 0),
        )
    )

    return BoardOut(
        rows=rows,
        live_matches=filled,
        unmet_demand=unmet,
        opportunity=round(opportunity, 2),
        currency=currency,
        supply_rows=len(supply),
    )


def _summarise(option: MatchOption, names: dict[str, str | None]) -> str:
    """'Al Manar 40 + Gulf Cell 60' — who supplies how many, in one line."""
    return " + ".join(
        f"{names.get(a.counterparty_id) or a.counterparty_id[:8]} {a.quantity}"
        for a in option.allocations[:3]
    )


def _note(best: MatchOption | None, near: list[MatchOption]) -> str | None:
    if best is None:
        return f"{len(near)} near miss(es), nothing exact" if near else "no supply on the board"
    parts = []
    if best.shortfall:
        parts.append(f"short by {best.shortfall}")
    # The usual case on a real board: a WTB list says what is wanted, not what will be
    # paid. Saying so is more use than an empty column, because the buy price is known
    # and it is the half of the trade the client controls.
    if best.total_margin is None:
        parts.append("buyer stated no price")
    parts.extend(best.relaxed[:1])
    return "; ".join(parts) or None


@router.get("/fill", response_model=MatchesOut, summary="Ways to fill a buyer requirement")
def fill(
    offer_id: str = Query(..., description="id of a buy-side row"),
    include_near_misses: bool = Query(True),
    context=Depends(current_tenant),
) -> MatchesOut:
    tenant_id, repository = context
    subject = _require(repository.get_board_offer(tenant_id, offer_id))

    if subject.side != "buy":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that row is not a buyer requirement")

    supply = [_supply(o) for o in repository.load_board(tenant_id, "sell")]
    options = fill_requirement(
        _requirement(subject), supply, include_near_misses=include_near_misses
    )
    return _response(subject, options, "fill", repository, tenant_id)


@router.get("/place", response_model=MatchesOut, summary="Ways to place a seller's lot")
def place(
    offer_id: str = Query(..., description="id of a sell-side row"),
    context=Depends(current_tenant),
) -> MatchesOut:
    tenant_id, repository = context
    subject = _require(repository.get_board_offer(tenant_id, offer_id))

    if subject.side != "sell":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that row is not a seller offer")

    demand = [_requirement(o) for o in repository.load_board(tenant_id, "buy")]
    options = place_offer(_supply(subject), demand)
    return _response(subject, options, "place", repository, tenant_id)


# ---------------------------------------------------------------- plumbing


def _require(offer: BoardOffer | None) -> BoardOffer:
    if offer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such offer on this board")
    return offer


def _requirement(offer: BoardOffer) -> Requirement:
    return Requirement(
        id=offer.id,
        counterparty_id=offer.counterparty_id,
        quantity=offer.quantity,
        unit_price=offer.unit_price,
        currency=offer.currency,
        identity_key=offer.identity_key,
        ean=offer.ean,
        brand=offer.brand,
        description=offer.description,
        description_key=offer.description_key or "",
        match_key=offer.match_key or build_match_key(offer.description),
        capacity_gb=offer.capacity_gb,
        colour=offer.colour,
        country=offer.country,
        category=offer.category,
        # Read from the description rather than stored, for the same reason as
        # match_key: it is derived, and a derived value in the database goes stale the
        # moment the derivation changes.
        accepted_colours=_accepted_colours(offer.description, offer.colour),
    )


def _accepted_colours(description: str, colour: str | None) -> tuple[str, ...]:
    """Every finish named in a buyer's line.

    'IPHONE 17 PRO MAX 256GB blue / silver / orange' is one order for 50 units that any
    of three finishes can fill. Stored, it keeps only the first — colour is part of
    identity and a row can hold one — so the other two are recovered here.
    """
    found = [v.colour for v in expand_variants(description or "") if v.colour]
    if colour and colour not in found:
        found.append(colour)
    return tuple(dict.fromkeys(found))


def _supply(offer: BoardOffer) -> Supply:
    return Supply(
        id=offer.id,
        counterparty_id=offer.counterparty_id,
        quantity=offer.quantity,
        unit_price=offer.unit_price,
        currency=offer.currency,
        identity_key=offer.identity_key,
        ean=offer.ean,
        brand=offer.brand,
        description=offer.description,
        description_key=offer.description_key or "",
        match_key=offer.match_key or build_match_key(offer.description),
        capacity_gb=offer.capacity_gb,
        colour=offer.colour,
        country=offer.country,
        category=offer.category,
    )


def _response(
    subject: BoardOffer,
    options: list[MatchOption],
    direction: str,
    repository,
    tenant_id: str,
) -> MatchesOut:
    # Counterparty names are looked up once for the whole response rather than per
    # allocation: a combination of three suppliers would otherwise be three round trips.
    names = _counterparty_names(repository, tenant_id, options)

    groups = {
        name: [_option(option, names) for option in items]
        for name, items in group_options(options).items()
    }

    return MatchesOut(
        subject_id=subject.id,
        subject_description=subject.description,
        subject_quantity=subject.quantity,
        subject_price=subject.unit_price,
        subject_currency=subject.currency,
        direction=direction,
        groups=groups,
        total_options=len(options),
    )


def _counterparty_names(repository, tenant_id: str, options: list[MatchOption]) -> dict[str, str]:
    ids = {a.counterparty_id for option in options for a in option.allocations}
    if not ids:
        return {}

    try:
        rows = (
            repository.client.table("counterparties")
            .select("id,name,primary_email")
            .eq("tenant_id", tenant_id)
            .in_("id", list(ids))
            .execute()
        ).data
    except Exception:  # the in-memory repository has no client; names are cosmetic
        return {}

    return {r["id"]: r.get("name") or r.get("primary_email") or r["id"] for r in rows}


def _option(option: MatchOption, names: dict[str, str]) -> OptionOut:
    return OptionOut(
        kind=option.kind,
        allocations=[
            AllocationOut(
                offer_id=a.offer_id,
                counterparty_id=a.counterparty_id,
                counterparty_name=names.get(a.counterparty_id),
                quantity=a.quantity,
                unit_price=a.unit_price,
                currency=a.currency,
                description=a.description,
            )
            for a in option.allocations
        ],
        requested_quantity=option.requested_quantity,
        filled_quantity=option.filled_quantity,
        shortfall=option.shortfall,
        supplier_count=option.supplier_count,
        blended_unit_cost=option.blended_unit_cost,
        counterpart_unit_price=option.buyer_unit_price,
        unit_margin=option.unit_margin,
        total_margin=option.total_margin,
        margin_pct=option.margin_pct,
        relaxed=option.relaxed,
        warnings=option.warnings,
    )
