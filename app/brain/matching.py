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

from dataclasses import dataclass, field
from itertools import combinations
from typing import Literal

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


@dataclass(frozen=True)
class Allocation:
    """One leg of a match.

    `counterparty_id` is always **the other party in this leg**, which means the
    supplier when filling a requirement and the buyer when placing a lot. The two
    directions are mirror images, and naming it 'supplier' would be wrong half the time.

    `unit_price` follows the same logic: the price being paid to the supplier when
    filling, the price being charged to the buyer when placing.
    """

    supply_id: str
    counterparty_id: str
    quantity: int
    unit_price: float
    currency: str | None
    description: str


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
    candidates = [
        s
        for s in supply
        # A counterparty buying and selling the same thing is not a deal; it is the
        # same firm on both sides of the board.
        if s.counterparty_id != requirement.counterparty_id
        and (s.quantity or 0) > 0
        and s.unit_price is not None
    ]

    exact = [s for s in candidates if _is_exact(requirement, s)]
    options: list[MatchOption] = []

    for item in exact:
        options.append(_single(requirement, item))

    if requirement.quantity:
        options.extend(_combinations(requirement, exact, max_suppliers))

    if include_near_misses:
        near = [s for s in candidates if not _is_exact(requirement, s) and _is_near(requirement, s)]
        for item in near:
            option = _single(requirement, item)
            option.kind = "near_miss"
            option.relaxed = _relaxations(requirement, item)
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

    # Best price first — this is the seller's side, so revenue is what is maximised.
    interested.sort(key=lambda r: (r.unit_price or 0), reverse=True)

    options: list[MatchOption] = []
    for buyer in interested:
        options.append(_single_placement(buyer, offer))

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
                supply_id=offer.id,
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
        if not mismatch and blended_revenue is not None:
            unit_margin = round(blended_revenue - (offer.unit_price or 0), 4)

        split = MatchOption(
            kind="combination",
            requirement_id=offer.id,
            allocations=allocations,
            requested_quantity=offer.quantity,
            filled_quantity=placed,
            blended_unit_cost=offer.unit_price,
            buyer_unit_price=blended_revenue,
            unit_margin=unit_margin,
            total_margin=None if mismatch else round(revenue - cost, 2),
            warnings=["currencies differ; margin not computed"] if mismatch else [],
        )
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
                supply_id=item.id,
                counterparty_id=item.counterparty_id,
                quantity=take,
                unit_price=item.unit_price or 0.0,
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

    if mismatch:
        option.warnings.append(
            f"buyer quotes {requirement.currency}, seller quotes {item.currency}; "
            f"margin not computed"
        )
    if requirement.unit_price is None:
        option.warnings.append("buyer stated no price; margin unknown")

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
                supply_id=offer.id,
                counterparty_id=buyer.counterparty_id,
                quantity=take,
                unit_price=buyer.unit_price or 0.0,
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
        option.warnings.append(
            f"buyer quotes {buyer.currency}, seller quotes {offer.currency}; "
            f"margin not computed"
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

    pool = sorted(exact, key=lambda s: s.unit_price or 0)[:COMBINATION_POOL]
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
    if sweep is not None and tuple(sorted(a.supply_id for a in sweep.allocations)) not in seen:
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

    for item in sorted(exact, key=lambda s: s.unit_price or 0):
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

    for item in sorted(group, key=lambda s: s.unit_price or 0):
        if remaining <= 0:
            break
        take = min(remaining, item.quantity or 0)
        if take <= 0:
            continue
        allocations.append(
            Allocation(
                supply_id=item.id,
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
        option.warnings.append(
            f"crosses {len(countries)} countries ({', '.join(sorted(countries))}); "
            f"freight and duty can erode the apparent gain"
        )
    if mismatch:
        option.warnings.append("suppliers quote different currencies; margin not computed")

    return option


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
    if not _compatible(requirement, item):
        return False

    if requirement.ean and item.ean:
        return requirement.ean == item.ean

    # Both keys derived the same way: comparing them is meaningful.
    if (
        requirement.identity_key.startswith("spec:")
        and item.identity_key.startswith("spec:")
        and requirement.identity_key == item.identity_key
    ):
        return True

    left, right = _comparable_keys(requirement, item)
    return bool(
        left
        and left == right
        and requirement.capacity_gb == item.capacity_gb
        and _colour_ok(requirement, item)
    )


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
    if not _compatible(requirement, item):
        return False

    left, right = _comparable_keys(requirement, item)
    if not left or left != right:
        return False

    differences = 0
    if requirement.capacity_gb != item.capacity_gb:
        differences += 1
    if not _colour_ok(requirement, item):
        differences += 1

    return 0 < differences <= 2


def _relaxations(requirement: Requirement, item: Supply) -> list[str]:
    out = []
    if requirement.capacity_gb != item.capacity_gb:
        out.append(f"capacity {item.capacity_gb}GB, wanted {requirement.capacity_gb}GB")
    if not _colour_ok(requirement, item):
        out.append(f"colour {item.colour}, wanted {requirement.colour}")
    return out


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
