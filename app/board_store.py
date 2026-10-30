"""The match board, worked out once and kept (table `board_matches`, migration 0017).

/matches/board computes the whole board on every request. This computes it when it
changes and stores the answer, so the dashboard reads rows instead of waiting for the
engine. Two ways in:

- **refresh(full=False)** — after anything writes an offer. Reads the offers and
  counterparties changed since the last refresh and recomputes only the requirements
  they can touch: a changed requirement itself; every requirement the matching engine
  relates (`app.brain.matching.related`) to a changed lot, or to any lot of a supplier
  whose list changed, since a sibling line can change how a lot is read; every
  requirement whose stored options used a changed lot or counterparty, which covers a
  lot edited until it no longer resembles anything; and every requirement with no
  stored row yet.
- **refresh(full=True)** — everything. On startup (so a deploy that changes the matching
  rules reaches every row), every few hours as a safety net, when the model
  equivalences change (an alias can join any two products), and on demand from
  POST /matches/rebuild.

Rows are only written when their content changed, so an email that touches nothing
produces no writes and no realtime events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.api.matches import (
    _STATE_ORDER,
    _aliases,
    _legs,
    _requirement,
    _summarise,
    _supplier_ids,
    _supply,
    build_row,
    infer_networks,
)
from app.brain.matching import MatchOption, Requirement, fill_requirement, related
from app.db.models import BoardOffer

log = logging.getLogger(__name__)

# One refresh at a time per process. The middleware, the background loop and the
# rebuild endpoint can all ask at once; two refreshes racing would each write half a board.
_LOCK = threading.Lock()

# How many real options are stored per requirement, beyond each supplier's best one.
_TOP_OPTIONS = 5


@dataclass
class ChangedOffer:
    offer: BoardOffer
    status: str
    updated_at: str


@dataclass
class RefreshResult:
    tenant_id: str
    full: bool
    examined: int = 0
    recomputed: int = 0
    written: int = 0
    deleted: int = 0
    newly_matched: list[str] = field(default_factory=list)
    skipped: bool = False


# ---------------------------------------------------------------- storage


class SupabaseBoardDB:
    """The queries the store needs, against Supabase with the service role."""

    def __init__(self, repository):
        self.repository = repository
        self.client = repository.client

    def tenant_ids(self) -> list[str]:
        rows = self.client.table("tenants").select("id").execute().data or []
        return [r["id"] for r in rows]

    def load_board(self, tenant_id: str, side: str) -> list[BoardOffer]:
        return self.repository.load_board(tenant_id, side)

    def aliases(self, tenant_id: str) -> dict[str, str]:
        return _aliases(self.repository, tenant_id)

    def state(self, tenant_id: str) -> dict | None:
        rows = (
            self.client.table("board_match_state").select("*")
            .eq("tenant_id", tenant_id).limit(1).execute()
        ).data
        return rows[0] if rows else None

    def save_state(self, tenant_id: str, values: dict) -> None:
        self.client.table("board_match_state").upsert(
            {"tenant_id": tenant_id, **values, "updated_at": _now()}, on_conflict="tenant_id"
        ).execute()

    def stored(self, tenant_id: str) -> dict[str, dict]:
        from app.db.supabase_repo import _all_rows

        rows = _all_rows(
            lambda: self.client.table("board_matches")
            .select("offer_id,buyer_id,supplier_ids,offer_ids,first_matched_at,content_hash")
            .eq("tenant_id", tenant_id)
            .order("offer_id")
        )
        return {r["offer_id"]: r for r in rows}

    def write(self, rows: list[dict]) -> None:
        for start in range(0, len(rows), 200):
            self.client.table("board_matches").upsert(
                rows[start:start + 200], on_conflict="offer_id"
            ).execute()

    def delete(self, tenant_id: str, offer_ids: list[str]) -> None:
        for start in range(0, len(offer_ids), 200):
            (
                self.client.table("board_matches").delete()
                .eq("tenant_id", tenant_id)
                .in_("offer_id", offer_ids[start:start + 200])
                .execute()
            )

    def latest(self, table: str, tenant_id: str) -> str | None:
        rows = (
            self.client.table(table).select("updated_at").eq("tenant_id", tenant_id)
            .order("updated_at", desc=True).limit(1).execute()
        ).data
        return rows[0]["updated_at"] if rows else None

    def changed_offers(self, tenant_id: str, since: str) -> list[ChangedOffer]:
        from app.db.supabase_repo import _BOARD_COLUMNS, _all_rows, _board_offer

        rows = _all_rows(
            lambda: self.client.table("offers")
            .select(_BOARD_COLUMNS + ",status,updated_at")
            .eq("tenant_id", tenant_id)
            .gt("updated_at", since)
            .order("updated_at")
            .order("id")
        )
        return [ChangedOffer(_board_offer(r), r["status"], r["updated_at"]) for r in rows]

    def changed_counterparties(self, tenant_id: str, since: str) -> list[tuple[str, str]]:
        rows = (
            self.client.table("counterparties").select("id,updated_at")
            .eq("tenant_id", tenant_id).gt("updated_at", since)
            .order("updated_at").limit(5000).execute()
        ).data or []
        return [(r["id"], r["updated_at"]) for r in rows]


# ---------------------------------------------------------------- refresh


def refresh(db, tenant_id: str, *, full: bool = False) -> RefreshResult:
    """Bring one tenant's stored board up to date. See the module docstring."""
    with _LOCK:
        return _refresh(db, tenant_id, full=full)


def _refresh(db, tenant_id: str, *, full: bool) -> RefreshResult:
    state = db.state(tenant_id)
    first_build = state is None
    aliases = db.aliases(tenant_id)
    fingerprint = _fingerprint(aliases)

    if state is None or fingerprint != state.get("alias_fingerprint"):
        full = True

    # Watermarks are read *before* the board, so a change landing mid-refresh is newer
    # than the mark and is picked up by the next one rather than lost between them.
    if full:
        offers_mark = db.latest("offers", tenant_id)
        people_mark = db.latest("counterparties", tenant_id)
        changed: list[ChangedOffer] = []
        changed_people: set[str] = set()
    else:
        offers_since = state.get("offers_watermark") or "1970-01-01T00:00:00+00:00"
        people_since = state.get("counterparties_watermark") or "1970-01-01T00:00:00+00:00"
        changed = db.changed_offers(tenant_id, offers_since)
        people = db.changed_counterparties(tenant_id, people_since)
        changed_people = {cid for cid, _ in people}
        offers_mark = max([c.updated_at for c in changed], default=offers_since, key=_when0)
        people_mark = max([t for _, t in people], default=people_since, key=_when0)

        if not changed and not changed_people:
            db.save_state(tenant_id, {"last_refresh_at": _now()})
            return RefreshResult(tenant_id, full=False, skipped=True)

    demand = db.load_board(tenant_id, "buy")
    supply_offers = db.load_board(tenant_id, "sell")
    supply = infer_networks([_supply(o, aliases) for o in supply_offers])
    stored = db.stored(tenant_id)

    requirements: dict[str, Requirement] = {}

    def requirement(offer: BoardOffer) -> Requirement:
        if offer.id not in requirements:
            requirements[offer.id] = _requirement(offer, aliases)
        return requirements[offer.id]

    if full:
        scope = {o.id for o in demand}
    else:
        scope = _scope(demand, supply, stored, changed, changed_people, aliases, requirement)

    names = {o.counterparty_id: o.counterparty_name for o in supply_offers}
    first_seen = {o.id: o.first_seen_at for o in supply_offers}
    now = _now()
    result = RefreshResult(tenant_id, full=full, examined=len(demand))

    rows: list[dict] = []
    for offer in demand:
        if offer.id not in scope:
            continue
        options = fill_requirement(requirement(offer), supply)
        real = [o for o in options if o.kind != "near_miss"]
        near = [o for o in options if o.kind == "near_miss"]
        row = to_record(tenant_id, offer, real, near, names, first_seen)
        result.recomputed += 1

        previous = stored.get(offer.id) or {}
        fillable = row["state"] in ("filled", "short")
        if previous.get("first_matched_at"):
            row["first_matched_at"] = previous["first_matched_at"]
        elif fillable:
            # The first build learns the board as it stands, dated by when each match
            # became possible, so nobody is alerted to a week of old matches. After
            # that, a requirement turning fillable is news as of now.
            row["first_matched_at"] = (row["matched_at"] or now) if first_build else now
            if not first_build:
                result.newly_matched.append(offer.id)
        else:
            row["first_matched_at"] = None

        if previous.get("content_hash") == row["content_hash"] and (
            previous.get("first_matched_at") == row["first_matched_at"]
        ):
            continue
        row["computed_at"] = now
        rows.append(row)

    if rows:
        db.write(rows)
    result.written = len(rows)

    live = {o.id for o in demand}
    gone = [offer_id for offer_id in stored if offer_id not in live]
    if gone:
        db.delete(tenant_id, gone)
    result.deleted = len(gone)

    values: dict[str, Any] = {
        "offers_watermark": offers_mark,
        "counterparties_watermark": people_mark,
        "alias_fingerprint": fingerprint,
        "supply_rows": len(supply_offers),
        "last_refresh_at": now,
    }
    if full:
        values["last_full_at"] = now
    db.save_state(tenant_id, values)

    log.info(
        "board refreshed tenant=%s full=%s examined=%d recomputed=%d written=%d deleted=%d "
        "new_matches=%d",
        tenant_id, full, result.examined, result.recomputed, result.written, result.deleted,
        len(result.newly_matched),
    )
    return result


def _scope(demand, supply, stored, changed, changed_people, aliases, requirement) -> set[str]:
    """Which requirements a set of changes can touch."""
    changed_ids = {c.offer.id for c in changed}
    scope = {c.offer.id for c in changed if c.offer.side == "buy"}

    # Every lot of a supplier whose list changed, not just the changed lines: how one
    # line is read can depend on its siblings (infer_networks reads an unlabelled model
    # as 4G when the same list labels another 5G).
    touched = {c.offer.counterparty_id for c in changed if c.offer.side == "sell"}
    touched |= changed_people
    probe = [s for s in supply if s.counterparty_id in touched]
    # A lot that has just closed is no longer in the supply, but the requirements that
    # resembled it still need to lose it.
    live_supply = {s.id for s in supply}
    probe += [
        _supply(c.offer, aliases)
        for c in changed
        if c.offer.side == "sell" and c.offer.id not in live_supply
    ]

    for offer in demand:
        if offer.id in scope:
            continue
        previous = stored.get(offer.id)
        if (
            previous is None
            or offer.counterparty_id in changed_people
            or changed_ids.intersection(previous.get("offer_ids") or [])
            or changed_people.intersection(previous.get("supplier_ids") or [])
        ):
            scope.add(offer.id)
            continue
        if probe:
            wanted = requirement(offer)
            if any(related(wanted, item) for item in probe):
                scope.add(offer.id)
    return scope


# ---------------------------------------------------------------- records


def to_record(
    tenant_id: str,
    offer: BoardOffer,
    real: list[MatchOption],
    near: list[MatchOption],
    names: dict[str, str | None],
    first_seen: dict[str, str | None],
) -> dict:
    """One `board_matches` row. Its content hash covers everything but the timestamps
    this module manages, so an unchanged requirement is recognised and not rewritten."""
    shown = build_row(offer, real, near, names, first_seen)

    stored_options = _stored_options(real)
    legs = [a for option in real + near for a in option.allocations]
    record: dict[str, Any] = {
        "offer_id": offer.id,
        "tenant_id": tenant_id,
        "buyer_id": offer.counterparty_id,
        "description": shown.description,
        "spec_parts": shown.spec_parts,
        "counterparty_name": shown.counterparty_name,
        "country": shown.country,
        "quantity": shown.quantity,
        "unit_price": shown.unit_price,
        "currency": shown.currency,
        "brand": offer.brand,
        "category": offer.category,
        "search_text": _search_text(offer),
        "state": shown.state,
        "state_rank": _STATE_ORDER[shown.state],
        "option_count": shown.option_count,
        "near_miss_count": shown.near_miss_count,
        "best_summary": shown.best_summary,
        "best_legs": [leg.model_dump() for leg in shown.best_legs],
        "best_currency": shown.best_currency,
        "blended_unit_cost": _round(shown.blended_unit_cost),
        "total_margin": _round(shown.total_margin),
        "margin_pct": _round(shown.margin_pct),
        "shortfall": shown.shortfall,
        "filled_quantity": shown.filled_quantity,
        "note": shown.note,
        "options": [_option_record(o, names) for o in stored_options],
        "supplier_ids": list(dict.fromkeys(a.counterparty_id for a in legs)),
        "offer_ids": list(dict.fromkeys(a.offer_id for a in legs)),
        "matched_at": shown.matched_at,
        "last_confirmed_at": shown.last_confirmed_at,
    }
    record["content_hash"] = _hash(record)
    return record


def _stored_options(real: list[MatchOption]) -> list[MatchOption]:
    """The top few options, plus each supplier's best one.

    The supplier filter shows the best option a supplier is part of. Keeping the top
    five alone would lose that for a supplier who first appears sixth; keeping every
    combination would store hundreds per requirement.
    """
    keep: list[MatchOption] = list(real[:_TOP_OPTIONS])
    covered = {a.counterparty_id for o in keep for a in o.allocations}
    for option in real[_TOP_OPTIONS:]:
        new = {a.counterparty_id for a in option.allocations} - covered
        if new:
            keep.append(option)
            covered |= new
    return keep


def _option_record(option: MatchOption, names: dict[str, str | None]) -> dict:
    return {
        "kind": option.kind,
        "supplier_ids": _supplier_ids(option),
        "legs": [leg.model_dump() for leg in _legs(option, names)],
        "summary": _summarise(option, names),
        "currency": option.allocations[0].currency if option.allocations else None,
        "blended_unit_cost": _round(option.blended_unit_cost),
        "total_margin": _round(option.total_margin),
        "margin_pct": _round(option.margin_pct),
        "shortfall": option.shortfall,
        "filled_quantity": option.filled_quantity,
    }


def _search_text(offer: BoardOffer) -> str:
    capacity = f"{offer.capacity_gb}gb" if offer.capacity_gb else None
    parts = (
        offer.description, offer.model, offer.ean, offer.brand, capacity, offer.colour,
        offer.counterparty_name,
    )
    return " ".join(p for p in parts if p).casefold()


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _hash(record: dict) -> str:
    content = {k: v for k, v in record.items() if k not in {"content_hash", "computed_at"}}
    blob = json.dumps(content, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


def _fingerprint(aliases: dict[str, str]) -> str:
    blob = json.dumps(sorted(aliases.items()), separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _when0(stamp: str) -> float:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
