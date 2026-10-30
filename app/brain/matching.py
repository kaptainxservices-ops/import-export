"""Pairing buyer requirements to seller offers.

This is the feature the client actually wants. A filterable list of offers is something
he could build in a spreadsheet; what he cannot do by hand is notice, across two hundred
rows from eleven suppliers, that nobody has the hundred units a buyer wants but three
suppliers together do.

Two directions, both first-class:

    fill_requirement   one buyer's requirement  <- many seller offers
    place_offer        one seller's lot          -> many buyers

The reverse direction is not an afterthought. Splitting a 200-unit lot across four
buyers who each want 50 usually earns more than selling it whole to the one buyer who
can take it all, because the buyer who can absorb everything knows it and prices
accordingly.

Four rules run through the whole module.

**Never promise stock that does not exist.** A fill is capped at what is actually
available, and a shortfall is stated rather than rounded away.

**Never invent an exchange rate.** Where buyer and seller quote different currencies the
margin is left as None and the option is flagged. A guessed rate produces a plausible
number that is wrong by whatever the rate has moved, and nobody checks a plausible
number.

**Near-misses are surfaced, never silently dropped.** A buyer wanting 256GB when only
512GB exists is a phone call, not a dead end. They are grouped and labelled so the
reason for the mismatch is visible.

**The highest raw margin is not automatically the best option.** A three-supplier
cross-border combination carries execution risk that a single local supplier does not,
so ranking penalises both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Literal

from app.brain.normalise.canonical import is_descriptive
from app.brain.normalise.regions import regions_conflict

MatchKind = Literal["exact", "combination", "partial", "near_miss"]

# How many suppliers may be aggregated into one fill. Each additional supplier is
# another negotiation, another shipment and another chance of the lot vanishing before
# the deal closes.
MAX_SUPPLIERS = 3

# Only the cheapest few candidates are considered for combinations. Exhaustive search
# over a 200-row board is both slow and pointless: an option built from the eighth
# cheapest supplier will never win on margin.
COMBINATION_POOL = 8

# Ranking penalties, applied to margin.
SUPPLIER_PENALTY = 0.05      # per supplier beyond the first
CROSS_BORDER_PENALTY = 0.10  # once, if the combination spans countries
PARTIAL_PENALTY = 0.15       # a shortfall usually means another supplier still to find


@dataclass(frozen=True)
class Requirement:
    """What a buyer wants."""

    id: str
    counterparty_id: str
    quantity: int | None = None
    unit_price: float | None = None      # what they are willing to pay
    currency: str | None = None
    identity_key: str = ""
    ean: str | None = None
    brand: str | None = None
    description: str = ""
    description_key: str = ""
    capacity_gb: int | None = None
    colour: str | None = None
    country: str | None = None
    match_key: str = ""
    # A hard gate, not a hint. See _compatible.
    category: str | None = None
    # Every colour the buyer said yes to. 'IPHONE 17 PRO MAX 256GB blue / silver /
    # orange' is one order for 50 units that three finishes can fill, not an order for
    # blue that silver nearly fills. Empty means only `colour` is acceptable.
    accepted_colours: tuple[str, ...] = ()
    # The model, written one way however the line wrote it ('SAM S23' and 'Samsung
    # Galaxy S23' are both 'samsung:s23'), and the words it was built from. See
    # app/brain/normalise/canonical.py.
    model_key: str = ""
    model_tokens: tuple[str, ...] = ()
    # What a lot is, beyond which product: compared when both sides state them.
    grade: str | None = None
    region_code: str | None = None
    ram_gb: int | None = None
    edition: str | None = None
    network: str | None = None
    connectivity: str | None = None
    # The line says new, sealed or BNIB: graded stock is a different offer.
    wants_new: bool = False


@dataclass(frozen=True)
class Supply:
    """What a seller has."""

    id: str
    counterparty_id: str
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    identity_key: str = ""
    ean: str | None = None
    brand: str | None = None
    description: str = ""
    description_key: str = ""
    capacity_gb: int | None = None
    colour: str | None = None
    country: str | None = None
    match_key: str = ""
    # A hard gate, not a hint. See _compatible.
    category: str | None = None
    # The model, written one way however the line wrote it ('SAM S23' and 'Samsung
    # Galaxy S23' are both 'samsung:s23'), and the words it was built from. See
    # app/brain/normalise/canonical.py.
    model_key: str = ""
    model_tokens: tuple[str, ...] = ()
    # What a lot is, beyond which product: compared when both sides state them.
    grade: str | None = None
    region_code: str | None = None
    ram_gb: int | None = None
    edition: str | None = None
    network: str | None = None
    connectivity: str | None = None
    # 'incoming' for stock still in transit, and what the supplier said about when.
    availability: str | None = None
    availability_note: str | None = None


@dataclass(frozen=True)
class Allocation:
    """One leg of a match.

    `counterparty_id` is always **the other party in this leg**, which means the
    supplier when filling a requirement and the buyer when placing a lot. The two
    directions are mirror images, and naming it 'supplier' would be wrong half the time.

    `unit_price` follows the same logic: the price being paid to the supplier when
    filling, the price being charged to the buyer when placing.

    `offer_id` obeys the same rule, and it is the one that used to break it: it is the
    id of the row **the counterparty owns** — the supplier's lot when filling, the
    buyer's requirement when placing. The dashboard opens that row on the Sellers or
    Buyers tab, so a placement leg naming the seller's own lot sent it looking for a
    sell row on the buy side, and it found nothing.
    """

    offer_id: str
    counterparty_id: str
    quantity: int
    # None for a lot the seller listed without a price: real stock, a phone call away
    # from a quote, and never to be shown as free.
    unit_price: float | None
    currency: str | None
    description: str


@dataclass(frozen=True)
class Difference:
    """One way a lot is not what the buyer asked for: what they have, what was wanted.

    Structured so the dashboard can lay the differences out as a comparison rather than
    a run of sentences. `text` is the same thing as one line, for everything that still
    reads `relaxed`.
    """

    field: str        # 'model' | 'capacity' | 'colour' | 'grade' | 'spec' | 'ram' | ...
    label: str        # 'Capacity'
    offered: str      # what the seller's lot is
    wanted: str       # what the buyer asked for
    text: str


@dataclass(frozen=True)
class Note:
    """Something a trader must know about an option, sorted by what it is about.

    'check'  -- about the lot itself, to settle before quoting (incoming, graded, no price)
    'margin' -- why the margin cannot be computed (no buyer price, two currencies)
    'risk'   -- about executing the deal (several countries)
    """

    kind: str
    text: str


@dataclass
class MatchOption:
    kind: MatchKind
    requirement_id: str
    allocations: list[Allocation] = field(default_factory=list)

    requested_quantity: int | None = None
    filled_quantity: int = 0

    blended_unit_cost: float | None = None
    buyer_unit_price: float | None = None
    unit_margin: float | None = None
    total_margin: float | None = None

    relaxed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The same information, structured: `relaxed` as a comparison, `warnings` by kind.
    differences: list[Difference] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    score: float = 0.0

    @property
    def supplier_count(self) -> int:
        return len({a.counterparty_id for a in self.allocations})

    @property
    def shortfall(self) -> int:
        if self.requested_quantity is None:
            return 0
        return max(0, self.requested_quantity - self.filled_quantity)

    @property
    def is_complete(self) -> bool:
        return self.shortfall == 0

    @property
    def margin_pct(self) -> float | None:
        if self.total_margin is None or not self.buyer_unit_price or not self.filled_quantity:
            return None
        revenue = self.buyer_unit_price * self.filled_quantity
        return (self.total_margin / revenue * 100) if revenue else None


def fill_requirement(
    requirement: Requirement,
    supply: list[Supply],
    *,
    max_suppliers: int = MAX_SUPPLIERS,
    include_near_misses: bool = True,
) -> list[MatchOption]:
    """Every way to fill one buyer requirement, best first."""
    # A lot listed without a price stays a candidate. 1,114 earbuds 'price on request'
    # are stock a buyer can be told about today; dropping them hid a third of one
    # supplier's incoming list from every buyer on the board. They are offered alone,
    # never inside a combination, because a blended cost with an unknown in it is not
    # a number.
    candidates = [
        s
        for s in supply
        # A counterparty buying and selling the same thing is not a deal; it is the
        # same firm on both sides of the board.
        if s.counterparty_id != requirement.counterparty_id
        and (s.quantity or 0) > 0
    ]

    exact = [s for s in candidates if _is_exact(requirement, s)]
    options: list[MatchOption] = []

    for item in exact:
        options.append(_single(requirement, item))

    if requirement.quantity:
        priced = [s for s in exact if s.unit_price is not None]
        options.extend(_combinations(requirement, priced, max_suppliers))

    if include_near_misses:
        near = [s for s in candidates if not _is_exact(requirement, s) and _is_near(requirement, s)]
        for item in near:
            option = _single(requirement, item)
            option.kind = "near_miss"
            option.differences = _relaxations(requirement, item)
            option.relaxed = [d.text for d in option.differences]
            options.append(option)

    for option in options:
        option.score = _score(option)

    options.sort(key=lambda o: (o.score if o.score is not None else float("-inf")), reverse=True)
    return options


def place_offer(
    offer: Supply,
    demand: list[Requirement],
    *,
    max_buyers: int = MAX_SUPPLIERS,
) -> list[MatchOption]:
    """Ways to place one seller's lot across buyers.

    Often worth more than selling the whole lot to a single buyer: the one buyer who can
    absorb everything knows they are the only one who can, and prices accordingly.
    """
    interested = [
        r
        for r in demand
        if r.counterparty_id != offer.counterparty_id
        and (r.quantity or 0) > 0
        and _is_exact(r, offer)
    ]
    lot_notes = _lot_notes(None, offer)

    # Best price first — this is the seller's side, so revenue is what is maximised.
    interested.sort(key=lambda r: (r.unit_price or 0), reverse=True)

    options: list[MatchOption] = []
    for buyer in interested:
        option = _single_placement(buyer, offer)
        for note in _lot_notes(buyer, offer):
            _note(option, "check", note)
        options.append(option)

    remaining = offer.quantity or 0
    allocations: list[Allocation] = []
    revenue = 0.0
    covered: list[Requirement] = []

    for buyer in interested[:max_buyers]:
        if remaining <= 0:
            break
        take = min(remaining, buyer.quantity or 0)
        if take <= 0:
            continue
        allocations.append(
            Allocation(
                offer_id=buyer.id,
                counterparty_id=buyer.counterparty_id,
                quantity=take,
                unit_price=buyer.unit_price or 0.0,
                currency=buyer.currency,
                description=buyer.description,
            )
        )
        revenue += take * (buyer.unit_price or 0.0)
        remaining -= take
        covered.append(buyer)

    if len(allocations) > 1:
        placed = sum(a.quantity for a in allocations)
        cost = placed * (offer.unit_price or 0.0)
        mismatch = any(_currency_mismatch(b.currency, offer.currency) for b in covered)

        blended_revenue = round(revenue / placed, 4) if placed else None
        unit_margin = None
        if not mismatch and blended_revenue is not None and offer.unit_price is not None:
            unit_margin = round(blended_revenue - offer.unit_price, 4)

        split = MatchOption(
            kind="combination",
            requirement_id=offer.id,
            allocations=allocations,
            requested_quantity=offer.quantity,
            filled_quantity=placed,
            blended_unit_cost=offer.unit_price,
            buyer_unit_price=blended_revenue,
            unit_margin=unit_margin,
            total_margin=(
                None if mismatch or offer.unit_price is None else round(revenue - cost, 2)
            ),
        )
        if mismatch:
            _note(split, "margin", "buyers quote in a different currency from the seller")
        for note in lot_notes:
            _note(split, "check", note)
        options.append(split)

    for option in options:
        option.score = _score(option)

    options.sort(key=lambda o: o.score, reverse=True)
    return options


# ---------------------------------------------------------------- construction


def _single(requirement: Requirement, item: Supply) -> MatchOption:
    take = min(requirement.quantity or item.quantity or 0, item.quantity or 0)
    mismatch = _currency_mismatch(requirement.currency, item.currency)

    unit_margin = None
    total_margin = None
    if not mismatch and requirement.unit_price is not None and item.unit_price is not None:
        unit_margin = round(requirement.unit_price - item.unit_price, 4)
        total_margin = round(unit_margin * take, 2)

    option = MatchOption(
        kind="exact" if take and take >= (requirement.quantity or 0) else "partial",
        requirement_id=requirement.id,
        allocations=[
            Allocation(
                offer_id=item.id,
                counterparty_id=item.counterparty_id,
                quantity=take,
                unit_price=item.unit_price,
                currency=item.currency,
                description=item.description,
            )
        ],
        requested_quantity=requirement.quantity,
        filled_quantity=take,
        blended_unit_cost=item.unit_price,
        buyer_unit_price=requirement.unit_price,
        unit_margin=unit_margin,
        total_margin=total_margin,
    )

    # One sentence about the margin, not two. A buyer who named no price has no margin
    # whatever the currencies; the currencies are then worth knowing for the quote.
    if requirement.unit_price is None:
        _note(
            option, "margin",
            "buyer stated no price"
            + (
                f"; their list is in {requirement.currency}, the seller's in {item.currency}"
                if mismatch else ""
            ),
        )
    elif mismatch:
        _note(
            option, "margin",
            f"buyer pays in {requirement.currency}, seller charges in {item.currency}; "
            "margin not computed, no exchange rate is assumed",
        )
    for note in _lot_notes(requirement, item):
        _note(option, "check", note)

    return option


def _single_placement(buyer: Requirement, offer: Supply) -> MatchOption:
    """One buyer taking part or all of a lot.

    The mirror of `_single`, and it exists separately because the allocation names the
    buyer here, not the seller. Collapsing the two would make `counterparty_id` mean
    different things depending on which function happened to build the option.
    """
    take = min(offer.quantity or 0, buyer.quantity or offer.quantity or 0)
    mismatch = _currency_mismatch(buyer.currency, offer.currency)

    unit_margin = None
    total_margin = None
    if not mismatch and buyer.unit_price is not None and offer.unit_price is not None:
        unit_margin = round(buyer.unit_price - offer.unit_price, 4)
        total_margin = round(unit_margin * take, 2)

    option = MatchOption(
        kind="exact" if take >= (offer.quantity or 0) else "partial",
        requirement_id=offer.id,
        allocations=[
            Allocation(
                offer_id=buyer.id,
                counterparty_id=buyer.counterparty_id,
                quantity=take,
                unit_price=buyer.unit_price,
                currency=buyer.currency,
                description=buyer.description or offer.description,
            )
        ],
        requested_quantity=offer.quantity,
        filled_quantity=take,
        blended_unit_cost=offer.unit_price,
        buyer_unit_price=buyer.unit_price,
        unit_margin=unit_margin,
        total_margin=total_margin,
    )

    if mismatch:
        _note(
            option, "margin",
            f"buyer pays in {buyer.currency}, seller charges in {offer.currency}; "
            "margin not computed, no exchange rate is assumed",
        )

    return option


def _combinations(
    requirement: Requirement, exact: list[Supply], max_suppliers: int
) -> list[MatchOption]:
    """Aggregate several suppliers to cover one requirement.

    Only attempted when no single supplier can cover it alone, and only over the
    cheapest few candidates — an option built from the eighth cheapest supplier will
    never win on margin, and the search space grows fast.
    """
    wanted = requirement.quantity or 0
    if wanted <= 0:
        return []

    if any((s.quantity or 0) >= wanted for s in exact):
        return []

    pool = sorted(exact, key=_price_order)[:COMBINATION_POOL]
    if len(pool) < 2:
        return []

    seen: set[tuple[str, ...]] = set()
    out: list[MatchOption] = []

    for size in range(2, min(max_suppliers, len(pool)) + 1):
        for group in combinations(pool, size):
            if sum(s.quantity or 0 for s in group) < wanted:
                continue

            key = tuple(sorted(s.id for s in group))
            # A three-supplier group that only needs two of them is the two-supplier
            # option with a passenger. Skip it; the smaller one already exists.
            if any(set(previous).issubset(key) for previous in seen):
                continue
            seen.add(key)

            out.append(_build_combination(requirement, group))

    sweep = _sweep(requirement, exact, max_suppliers)
    if sweep is not None and tuple(sorted(a.offer_id for a in sweep.allocations)) not in seen:
        out.append(sweep)

    return out


def _sweep(
    requirement: Requirement, exact: list[Supply], max_suppliers: int
) -> MatchOption | None:
    """Take the cheapest lots in order until the order is full or the stock runs out.

    Two things the exhaustive search above cannot do, both found on a real requirement
    for 50 iPhone 17 Pro Max that the board answered with 19.

    **The cap is on suppliers, not rows.** MAX_SUPPLIERS exists because each additional
    supplier is another negotiation, another shipment and another chance of the lot
    vanishing. Six lots from *one* supplier is one negotiation, and capping the group at
    three rows threw away half their stock.

    **A near-complete fill beats a small exact one.** The search above discards any group
    that cannot cover the order outright, so 19 + 18 + 4 + 3 + 3 + 2 = 49 of 50 was
    dropped and a lone 19 became the best offer. A buyer wanting 50 would rather hear
    'I have 49' than 'I have 19'.
    """
    wanted = requirement.quantity or 0
    if wanted <= 0 or len(exact) < 2:
        return None

    chosen: list[Supply] = []
    suppliers: set[str] = set()
    remaining = wanted

    for item in sorted(exact, key=_price_order):
        if remaining <= 0:
            break
        if item.counterparty_id not in suppliers and len(suppliers) >= max_suppliers:
            continue
        take = min(remaining, item.quantity or 0)
        if take <= 0:
            continue
        chosen.append(item)
        suppliers.add(item.counterparty_id)
        remaining -= take

    # One lot is not a combination — `_single` already covers it, and offering the same
    # thing twice under two names wastes a line of the trader's attention.
    if len(chosen) < 2:
        return None

    return _build_combination(requirement, tuple(chosen))


def _build_combination(requirement: Requirement, group: tuple[Supply, ...]) -> MatchOption:
    wanted = requirement.quantity or 0
    remaining = wanted
    allocations: list[Allocation] = []
    cost = 0.0

    for item in sorted(group, key=_price_order):
        if remaining <= 0:
            break
        take = min(remaining, item.quantity or 0)
        if take <= 0:
            continue
        allocations.append(
            Allocation(
                offer_id=item.id,
                counterparty_id=item.counterparty_id,
                quantity=take,
                unit_price=item.unit_price or 0.0,
                currency=item.currency,
                description=item.description,
            )
        )
        cost += take * (item.unit_price or 0.0)
        remaining -= take

    filled = sum(a.quantity for a in allocations)
    blended = round(cost / filled, 4) if filled else None
    mismatch = any(_currency_mismatch(requirement.currency, s.currency) for s in group)

    unit_margin = None
    total_margin = None
    if not mismatch and requirement.unit_price is not None and blended is not None:
        unit_margin = round(requirement.unit_price - blended, 4)
        total_margin = round(unit_margin * filled, 2)

    option = MatchOption(
        kind="combination",
        requirement_id=requirement.id,
        allocations=allocations,
        requested_quantity=wanted,
        filled_quantity=filled,
        blended_unit_cost=blended,
        buyer_unit_price=requirement.unit_price,
        unit_margin=unit_margin,
        total_margin=total_margin,
    )

    countries = {s.country for s in group if s.country}
    if len(countries) > 1:
        _note(
            option, "risk",
            f"crosses {len(countries)} countries ({', '.join(sorted(countries))}); "
            f"freight and duty can erode the apparent gain",
        )
    if mismatch:
        _note(option, "margin", "suppliers quote different currencies; margin not computed")
    for item in group:
        for note in _lot_notes(requirement, item):
            _note(option, "check", note)

    return option


def _note(option: MatchOption, kind: str, text: str) -> None:
    """Record a note once, in both shapes."""
    if text in option.warnings:
        return
    option.warnings.append(text)
    option.notes.append(Note(kind, text))


# ---------------------------------------------------------------- comparison


def _is_exact(requirement: Requirement, item: Supply) -> bool:
    """Same product.

    EAN settles it outright — but **only when both sides have one**, and in practice
    they usually do not. Sellers publish EANs in their price lists; buyers write want-to-buy
    lists by hand, as 'Apple iPhone 14 128GB White', with no barcode anywhere.

    That asymmetry is why identity keys are not compared directly here. A key built
    from an EAN and a key built from a spec can never be equal, however identical the
    products are, so comparing them matched almost nothing against the real sample —
    3 requirements out of 225. Falling back to the spec fields is what makes buyer
    lists usable at all.
    """
    return _product_relation(requirement, item) == "same" and not _differences(
        requirement, item
    )


ProductRelation = Literal["same", "close"] | None


def _product_relation(requirement: Requirement, item: Supply) -> ProductRelation:
    """Whether two lines name the same model, before any attribute of the lot.

    'same' is certain: one barcode, one identity, or one canonical model -- which is
    what lets 'SAM S23', 'Samsung S23' and 'Galaxy S23 5G' meet. 'close' is a model
    name that matches except for words the rules do not recognise as part of a name
    ('Nord CE6 Smooth' against 'Nord CE6'): offered as a near miss for a person to
    confirm, never as a fill. Variant words and numbers are never in that leftover --
    an S23 and an S23 Ultra are not close, they are different phones.
    """
    if not _compatible(requirement, item):
        return None

    if requirement.ean and item.ean and requirement.ean == item.ean:
        return "same"

    # Both keys derived the same way: comparing them is meaningful.
    if (
        requirement.identity_key.startswith("spec:")
        and item.identity_key.startswith("spec:")
        and _spec_product(requirement.identity_key) == _spec_product(item.identity_key)
        and _spec_product(item.identity_key)
    ):
        return "same"

    if requirement.model_key and item.model_key:
        return _model_relation(requirement, item)

    # Two barcodes that differ, and no model on one side to say otherwise.
    if requirement.ean and item.ean:
        return None

    left, right = _comparable_keys(requirement, item)
    return "same" if left and left == right else None


def related(requirement: Requirement, item: Supply) -> bool:
    """Whether matching could ever pair these two, as an exact fill or a near miss.

    Every option fill_requirement returns passes through _product_relation, so a lot
    this says no to cannot appear among a requirement's options. That is what lets the
    stored board (app/board_store.py) recompute only the requirements a changed lot can
    touch, using the engine's own rule rather than a second guess at it.
    """
    return _product_relation(requirement, item) is not None


def _spec_product(identity_key: str) -> str:
    """The brand and description part of a spec identity, without the lot qualifiers."""
    parts = identity_key.split("|")
    return "|".join(parts[:2]) if len(parts) > 2 and parts[1] else ""


def _model_relation(requirement: Requirement, item: Supply) -> ProductRelation:
    left_brand, _, left = requirement.model_key.partition(":")
    right_brand, _, right = item.model_key.partition(":")
    if left_brand and right_brand and left_brand != right_brand:
        return None
    if not left or not right:
        return None
    if left == right:
        return "same"

    a, b = set(requirement.model_tokens), set(item.model_tokens)
    if not a or not b:
        return None
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    if not small < large:
        return None
    extra = large - small
    if all(_descriptive(word) for word in extra) and any(
        any(c.isdigit() for c in word) for word in small
    ):
        return "close"
    return None


def _descriptive(word: str) -> bool:
    # Three letters at least for a near miss: a two-letter leftover is more often a
    # code than a description.
    return len(word) >= 3 and is_descriptive(word)


_NEW_WORDS = {"new", "sealed", "bnib", "brand new", "unopened"}


def _grade_key(value: str | None) -> str:
    return re.sub(r"[\s_\-]+", "", (value or "").lower())


def _gb(value: int | None) -> str:
    return f"{value}GB" if value else "not stated"


def _said(value: str | None) -> str:
    return value if value else "not stated"


def _differences(requirement: Requirement, item: Supply) -> list[Difference]:
    """Everything about this lot that is not what the buyer asked for, in words.

    Each check runs only when both sides state the attribute. A buyer who wrote 'S23'
    has not refused a 5G handset, and a list that does not mention its market has not
    claimed one -- an unknown is not a mismatch, it is something to ask about.
    """
    out: list[Difference] = []

    def add(name: str, label: str, offered: str, wanted: str, text: str) -> None:
        out.append(Difference(name, label, offered, wanted, text))

    # One barcode is one capacity and one finish, whatever either line omitted.
    same_barcode = bool(requirement.ean and item.ean and requirement.ean == item.ean)
    if not same_barcode:
        if requirement.capacity_gb != item.capacity_gb:
            offered, wanted = _gb(item.capacity_gb), _gb(requirement.capacity_gb)
            add("capacity", "Capacity", offered, wanted, f"capacity {offered}, wanted {wanted}")
        if not _colour_ok(requirement, item):
            offered, wanted = _said(item.colour), _said(requirement.colour)
            if requirement.accepted_colours:
                wanted = " / ".join(requirement.accepted_colours)
            add("colour", "Colour", offered, wanted, f"colour {offered}, wanted {wanted}")

    if requirement.grade and item.grade:
        if _grade_key(requirement.grade) != _grade_key(item.grade):
            add("grade", "Grade", item.grade, requirement.grade,
                f"grade {item.grade}, wanted {requirement.grade}")
    elif requirement.wants_new and item.grade and _grade_key(item.grade) not in ("new", "bnib"):
        add("grade", "Grade", f"{item.grade} (graded)", "new",
            f"grade {item.grade} (graded stock), wanted new")

    if regions_conflict(requirement.region_code, item.region_code):
        add("spec", "Spec", item.region_code or "", requirement.region_code or "",
            f"spec {item.region_code}, wanted {requirement.region_code}")

    if requirement.ram_gb and item.ram_gb and requirement.ram_gb != item.ram_gb:
        add("ram", "RAM", f"{item.ram_gb}GB", f"{requirement.ram_gb}GB",
            f"{item.ram_gb}GB RAM, wanted {requirement.ram_gb}GB")

    if requirement.network and item.network and requirement.network != item.network:
        add("network", "Network", item.network.upper(), requirement.network.upper(),
            f"{item.network.upper()}, wanted {requirement.network.upper()}")

    if (
        requirement.connectivity
        and item.connectivity
        and requirement.connectivity != item.connectivity
        and (requirement.category or item.category) in ("tablet", "wearable")
    ):
        add("connectivity", "Connectivity", item.connectivity, requirement.connectivity,
            f"{item.connectivity}, wanted {requirement.connectivity}")

    wanted_edition = (requirement.edition or "").lower()
    offered_edition = (item.edition or "").lower()
    if "enterprise" in (wanted_edition + offered_edition) and wanted_edition != offered_edition:
        offered = item.edition or "standard edition"
        wanted = requirement.edition or "standard"
        add("edition", "Edition", offered, wanted, f"{offered}, wanted {wanted}")

    return out


def _lot_notes(requirement: Requirement | None, item: Supply) -> list[str]:
    """What a trader must know before quoting this lot, though it does not stop a fill."""
    notes: list[str] = []
    if item.unit_price is None:
        notes.append("seller listed no price; ask for one")
    if item.availability == "incoming":
        notes.append(
            "incoming stock" + (f" ({item.availability_note})" if item.availability_note else "")
        )
    if item.grade and (requirement is None or not requirement.grade):
        notes.append(f"seller's lot is grade {item.grade}")
    if item.region_code and (requirement is None or not requirement.region_code):
        notes.append(f"{item.region_code} spec")
    # The buyer named a market or a grade and the seller's line is silent on it: not a
    # mismatch, but the first question to ask before quoting.
    if requirement is not None and requirement.region_code and not item.region_code:
        notes.append(f"seller did not state the spec; buyer wants {requirement.region_code}")
    if requirement is not None and requirement.grade and not item.grade:
        notes.append(f"seller did not state a grade; buyer wants {requirement.grade}")
    return notes


def _colour_ok(requirement: Requirement, item: Supply) -> bool:
    """Whether this supply's finish is one the buyer will take.

    Buyers name several: 'IPHONE 17 PRO MAX 256GB blue / silver / orange'. Read as a
    request for blue, the silver and orange rows become near misses — a different
    section of the drawer, and crucially not combinable — so a board holding 19 blue,
    20 silver and 10 orange offered 19 of 50 and called the rest a compromise. It was
    49 of 50 all along.
    """
    if _same(requirement.colour, item.colour):
        return True

    # A buyer who named no colour has not refused any. 'Samsung S24 FE 128GB x50' is
    # an order the board can fill in whatever finishes exist; reading it as an order
    # for no colour made every lot a near miss and every requirement look unfillable.
    if not (requirement.colour or "").strip() and not requirement.accepted_colours:
        return True
    if (requirement.colour or "").strip().lower() in {"mixed", "any", "assorted", "random"}:
        return True

    if requirement.accepted_colours and item.colour:
        return item.colour.strip().lower() in {
            c.strip().lower() for c in requirement.accepted_colours
        }

    return False


def _compatible(requirement: Requirement, item: Supply) -> bool:
    """Whether these two could be the same thing at all, before looking at the words.

    This exists because of a real row on the board: a buyer wanting 100 iPhone 15 128GB
    was offered a *silicone MagSafe case* at €8. Both descriptions reduce to the match
    key 'iphone 15', because the word iPhone in the case's name is describing what it
    fits, and every distinguishing word — silicone, magsafe, case — is exactly the kind
    of adjective the match key is built to discard.

    No amount of text comparison fixes that, because the text genuinely is similar. What
    separates them is that one is a phone and one is an accessory, and the parser already
    knows: `detect_category` labels the case correctly. Nothing was asking.

    So category and brand are gates rather than scores. A mismatch is not a weaker match
    to be ranked lower or shown as a near miss — it is a different product, and offering
    it wastes the trader's attention on every single requirement.

    A missing value never blocks: plenty of rows have no category, and refusing those
    would be worse than the problem being solved.
    """
    if requirement.category and item.category and requirement.category != item.category:
        return False

    if requirement.brand and item.brand and requirement.brand.lower() != item.brand.lower():
        return False

    return True


def _comparable_keys(requirement: Requirement, item: Supply) -> tuple[str, str]:
    """The looser match key when both sides have one, the strict key otherwise.

    Sellers write 'Apple iPhone 16 128GB Blue/Green/Ultramarin' and buyers write
    'Apple iPhone 16 128GB Blue'. The strict key preserves those colour words because
    it also serves identity, where discarding them would merge real stock. The match
    key drops them.
    """
    if requirement.match_key and item.match_key:
        return requirement.match_key, item.match_key
    return requirement.description_key, item.description_key


def _is_near(requirement: Requirement, item: Supply) -> bool:
    """Same product family, one attribute apart.

    Two different EANs alone do not make a near-miss — a case and a phone have
    different EANs too. The descriptions have to agree first.
    """
    relation = _product_relation(requirement, item)
    if relation is None:
        return False

    differences = len(_differences(requirement, item)) + (1 if relation == "close" else 0)
    return 0 < differences <= 2


def _relaxations(requirement: Requirement, item: Supply) -> list[Difference]:
    out: list[Difference] = []
    if _product_relation(requirement, item) == "close":
        out.append(Difference(
            "model", "Model", item.description, requirement.description,
            f"listed as '{item.description}'; check it is the same model",
        ))
    out.extend(_differences(requirement, item))
    return out


def _price_order(item: Supply) -> float:
    """Cheapest first, and an unpriced lot last -- never first as if it were free."""
    return item.unit_price if item.unit_price is not None else float("inf")


def _same(left: str | None, right: str | None) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()


def _currency_mismatch(left: str | None, right: str | None) -> bool:
    """Only a mismatch when both are known and differ.

    An unknown currency is not treated as a mismatch — it is treated as unknown, and
    the margin is computed. That is a deliberate line: inventing a conversion rate is
    worse than assuming two unlabelled prices are in the same currency, which is
    usually true within one supplier's list.
    """
    return bool(left and right and left != right)


# ---------------------------------------------------------------- ranking


def _score(option: MatchOption) -> float:
    """Margin adjusted for execution risk.

    Highest raw margin is not automatically best. Every extra supplier is another
    negotiation and another shipment, and a cross-border combination adds freight and
    customs that can erase the gain that made it look attractive.
    """
    if option.total_margin is None:
        # Unknown margin is the normal case here, not the exception: a WTB list says
        # what a buyer wants and almost never what they will pay. Returning one flat
        # value for all of them left every such requirement sorted by insertion order,
        # which is how a board holding 49 of the 50 units wanted offered 19 — the
        # single-supplier options are built first and nothing ever reordered them.
        #
        # So rank by how much of the order gets filled. Still below any known margin,
        # because a number the trader can act on beats one they have to ring up for.
        filled = option.filled_quantity or 0
        wanted = option.requested_quantity or filled or 1
        coverage = min(1.0, filled / wanted) if wanted else 0.0
        if option.kind == "near_miss":
            coverage *= 0.5
        # A lot with a price beats one without: one is a quote, the other a phone call.
        if any(a.unit_price is None for a in option.allocations):
            coverage *= 0.8
        return round(-1.0 + coverage * 0.9, 4)

    score = float(option.total_margin)

    extra_suppliers = max(0, option.supplier_count - 1)
    score *= (1 - SUPPLIER_PENALTY) ** extra_suppliers

    if any("crosses" in w for w in option.warnings):
        score *= 1 - CROSS_BORDER_PENALTY

    if option.shortfall:
        score *= 1 - PARTIAL_PENALTY

    if option.kind == "near_miss":
        # A near-miss is a conversation, not a fill. It should never outrank a genuine
        # option of similar value.
        score *= 0.5

    return round(score, 4)


def group_options(options: list[MatchOption]) -> dict[str, list[MatchOption]]:
    """Group for display: the client asked for labelled sections, not one flat list."""
    groups: dict[str, list[MatchOption]] = {
        "single supplier": [],
        "combinations": [],
        "partial fill": [],
        "different spec": [],
    }

    for option in options:
        if option.kind == "near_miss":
            groups["different spec"].append(option)
        elif option.kind == "combination":
            groups["combinations"].append(option)
        elif option.shortfall:
            groups["partial fill"].append(option)
        else:
            groups["single supplier"].append(option)

    return {name: items for name, items in groups.items() if items}
