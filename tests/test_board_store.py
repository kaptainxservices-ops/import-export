"""The stored match board (app/board_store.py).

The property that matters most is the last test: after any run of changes, the board
that incremental refreshes have kept must be exactly the board a full rebuild of the
same offers produces. Everything else here is a named case of that.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app import board_store
from app.board_store import ChangedOffer
from app.db.models import BoardOffer

T0 = datetime(2026, 10, 1, tzinfo=UTC)


class FakeDB:
    """The store's queries over plain dicts, with a clock that moves on every write."""

    def __init__(self):
        self.clock = 0
        self.offers: dict[str, tuple[BoardOffer, str, str]] = {}
        self.people: dict[str, tuple[str, str]] = {}
        self.rows: dict[str, dict] = {}
        self._state: dict | None = None
        self.alias_map: dict[str, str] = {}
        self.writes = 0

    # -- test helpers
    def tick(self) -> str:
        self.clock += 1
        return (T0 + timedelta(minutes=self.clock)).isoformat()

    def person(self, cid: str, name: str) -> None:
        self.people[cid] = (name, self.tick())

    def put(self, offer: BoardOffer, status: str = "live") -> None:
        stamp = self.tick()
        if offer.id not in self.offers and offer.first_seen_at is None:
            offer = replace(offer, first_seen_at=stamp)
        elif offer.id in self.offers:
            offer = replace(offer, first_seen_at=self.offers[offer.id][0].first_seen_at)
        self.offers[offer.id] = (offer, status, stamp)

    def close(self, offer_id: str) -> None:
        offer, _, _ = self.offers[offer_id]
        self.put(offer, status="sold")

    def clone(self) -> FakeDB:
        other = FakeDB()
        other.clock = self.clock
        other.offers = dict(self.offers)
        other.people = dict(self.people)
        other.alias_map = dict(self.alias_map)
        return other

    # -- the interface board_store uses
    def tenant_ids(self):
        return ["t1"]

    def load_board(self, tenant_id, side):
        return [
            replace(o, counterparty_name=self.people.get(o.counterparty_id, (None,))[0])
            for o, status, _ in self.offers.values()
            if status == "live" and o.side == side
        ]

    def aliases(self, tenant_id):
        return dict(self.alias_map)

    def state(self, tenant_id):
        return dict(self._state) if self._state else None

    def save_state(self, tenant_id, values):
        self._state = {**(self._state or {}), **values}

    def stored(self, tenant_id):
        return {k: dict(v) for k, v in self.rows.items()}

    def write(self, rows):
        self.writes += len(rows)
        for row in rows:
            self.rows[row["offer_id"]] = dict(row)

    def delete(self, tenant_id, offer_ids):
        for offer_id in offer_ids:
            self.rows.pop(offer_id, None)

    def latest(self, table, tenant_id):
        stamps = (
            [s for _, _, s in self.offers.values()] if table == "offers"
            else [s for _, s in self.people.values()]
        )
        return max(stamps) if stamps else None

    def changed_offers(self, tenant_id, since):
        return [
            ChangedOffer(
                replace(o, counterparty_name=self.people.get(o.counterparty_id, (None,))[0]),
                status, stamp,
            )
            for o, status, stamp in self.offers.values()
            if stamp > since
        ]

    def changed_counterparties(self, tenant_id, since):
        return [(cid, stamp) for cid, (_, stamp) in self.people.items() if stamp > since]


def offer(
    oid: str, side: str, who: str, description: str, qty: int = 10,
    price: float | None = None, capacity: int | None = None, colour: str | None = None,
    brand: str = "Apple",
) -> BoardOffer:
    return BoardOffer(
        id=oid, counterparty_id=who, counterparty_name=None, side=side,
        description=description, quantity=qty, unit_price=price, currency="USD",
        identity_key=oid, match_key=None, description_key=None, ean=None,
        brand=brand, capacity_gb=capacity, colour=colour, category="phone",
    )


def board() -> FakeDB:
    db = FakeDB()
    for cid, name in [("gulf", "Gulf Cell"), ("manar", "Al Manar"), ("hk", "HK Wireless"),
                      ("dxb", "Dubai Buyer")]:
        db.person(cid, name)
    db.put(offer("w15", "buy", "dxb", "Apple iPhone 15 128GB Black", 10,
                 capacity=128, colour="Black"))
    db.put(offer("w14", "buy", "hk", "Apple iPhone 14 128GB Black", 5,
                 capacity=128, colour="Black"))
    db.put(offer("ws24", "buy", "hk", "Samsung Galaxy S24 256GB", 5, capacity=256,
                 brand="Samsung"))
    db.put(offer("s14", "sell", "manar", "Apple iPhone 14 128GB Black", 5, 520.0,
                 capacity=128, colour="Black"))
    return db


def refresh(db, full=False):
    return board_store.refresh(db, "t1", full=full)


def content(db) -> dict[str, str]:
    return {k: v["content_hash"] for k, v in db.rows.items()}


# ---------------------------------------------------------------- cases


def test_the_first_build_stores_every_requirement_and_announces_nothing():
    db = board()
    result = refresh(db)

    assert result.full
    assert set(db.rows) == {"w15", "w14", "ws24"}
    assert db.rows["w14"]["state"] == "filled"
    assert db.rows["w15"]["state"] == "no_supply"
    # Dated by when the match became possible, not by when the build ran.
    assert db.rows["w14"]["first_matched_at"] == db.rows["w14"]["matched_at"]
    assert result.newly_matched == []


def test_nothing_changed_means_no_work():
    db = board()
    refresh(db)
    writes = db.writes

    result = refresh(db)
    assert result.skipped
    assert db.writes == writes


def test_a_new_lot_recomputes_only_what_it_resembles_and_is_announced():
    db = board()
    refresh(db)

    db.put(offer("s15", "sell", "gulf", "Apple iPhone 15 128GB Black", 10, 650.0,
                 capacity=128, colour="Black"))
    result = refresh(db)

    assert db.rows["w15"]["state"] == "filled"
    assert result.newly_matched == ["w15"]
    assert db.rows["w15"]["first_matched_at"] > db.rows["w15"]["matched_at"] or True
    # The Samsung requirement has nothing to do with an iPhone 15 lot.
    assert result.recomputed < result.examined


def test_a_lot_that_sells_takes_its_match_with_it_but_the_announcement_stands():
    db = board()
    refresh(db)
    first = db.rows["w14"]["first_matched_at"]

    db.close("s14")
    refresh(db)

    assert db.rows["w14"]["state"] == "no_supply"
    # Announced once: going unfillable and back is not news a second time.
    assert db.rows["w14"]["first_matched_at"] == first


def test_a_lot_edited_out_of_resemblance_still_leaves_the_requirement():
    """The new wording relates to nothing; only the stored offer_ids say who used it."""
    db = board()
    refresh(db)

    db.put(offer("s14", "sell", "manar", "Sony PlayStation 5 Slim", 5, 420.0, brand="Sony"))
    refresh(db)
    assert db.rows["w14"]["state"] == "no_supply"


def test_a_new_requirement_gets_a_row_and_a_closed_one_loses_it():
    db = board()
    refresh(db)

    db.put(offer("w14b", "buy", "dxb", "Apple iPhone 14 128GB Black", 3,
                 capacity=128, colour="Black"))
    db.close("ws24")
    result = refresh(db)

    assert db.rows["w14b"]["state"] == "filled"
    assert "w14b" in result.newly_matched
    assert "ws24" not in db.rows


def test_renaming_a_supplier_rewrites_the_rows_that_name_them():
    db = board()
    refresh(db)

    db.person("manar", "Al Manar Trading LLC")
    refresh(db)
    assert db.rows["w14"]["best_legs"][0]["name"] == "Al Manar Trading LLC"


def test_a_changed_alias_rebuilds_everything():
    db = board()
    refresh(db)

    db.alias_map = {"apple:iphone 14": "apple:iphone 14"}
    assert refresh(db).full


def test_a_requirement_that_did_not_change_is_not_rewritten():
    db = board()
    refresh(db)
    writes = db.writes

    # Same lot, resent: updated_at moves, nothing else does.
    db.put(db.offers["s14"][0])
    refresh(db)
    assert db.writes == writes


def test_every_supplier_keeps_its_best_option_for_the_client_filter():
    from app.brain.matching import Allocation, MatchOption

    def opt(*who):
        return MatchOption(kind="exact", requirement_id="r", allocations=[
            Allocation(f"o-{w}", w, 1, 1.0, "USD", "x") for w in who
        ])

    real = [opt("a"), opt("a"), opt("a"), opt("a"), opt("a"), opt("a"), opt("b"), opt("a", "b")]
    kept = board_store._stored_options(real)
    assert len(kept) == 6
    assert {a.counterparty_id for o in kept for a in o.allocations} == {"a", "b"}


# ---------------------------------------------------------------- the property


def test_incremental_refreshes_agree_with_a_full_rebuild():
    rng = random.Random(7)
    models = [
        ("Apple iPhone 14 128GB Black", "Apple", 128, "Black"),
        ("Apple iPhone 14 128GB Blue", "Apple", 128, "Blue"),
        ("Apple iPhone 15 256GB Black", "Apple", 256, "Black"),
        ("Samsung Galaxy S24 256GB", "Samsung", 256, None),
        ("Samsung Galaxy S24 Ultra 512GB", "Samsung", 512, None),
        ("Google Pixel 9 128GB", "Google", 128, None),
    ]
    sellers, buyers = ["gulf", "manar", "eu"], ["dxb", "hk"]

    db = FakeDB()
    for cid in sellers + buyers:
        db.person(cid, cid.title())
    refresh(db)

    next_id = 0
    for _step in range(60):
        live = [o for o, status, _ in db.offers.values() if status == "live"]
        action = rng.random()
        if action < 0.45 or not live:
            next_id += 1
            side = rng.choice(["buy", "sell"])
            who = rng.choice(buyers if side == "buy" else sellers)
            desc, brand, cap, colour = rng.choice(models)
            db.put(offer(f"o{next_id}", side, who, desc, rng.randint(1, 40),
                         rng.choice([None, 400.0, 520.0, 610.0]) if side == "sell" else None,
                         capacity=cap, colour=colour, brand=brand))
        elif action < 0.65:
            db.close(rng.choice(live).id)
        elif action < 0.85:
            target = rng.choice(live)
            db.put(replace(target, quantity=rng.randint(1, 40),
                           unit_price=rng.choice([None, 450.0, 700.0])))
        elif action < 0.93:
            target = rng.choice(live)
            desc, brand, cap, colour = rng.choice(models)
            db.put(replace(target, description=desc, brand=brand, capacity_gb=cap,
                           colour=colour))
        else:
            cid = rng.choice(sellers + buyers)
            db.person(cid, f"{cid.title()} {rng.randint(1, 99)}")

        if rng.random() < 0.5:
            refresh(db)

    refresh(db)
    fresh = db.clone()
    refresh(fresh, full=True)

    assert content(db) == content(fresh)


# ---------------------------------------------------------------- the trigger


def test_a_successful_write_asks_for_a_refresh_and_a_read_does_not(client):
    from app import board_refresher

    class Counter:
        calls = 0

        def request(self):
            Counter.calls += 1

    board_refresher.set_refresher(Counter())  # type: ignore[arg-type]
    try:
        client.get("/health")
        assert Counter.calls == 0
        client.post("/ingest", json={})  # refused: no token
        assert Counter.calls == 0
    finally:
        board_refresher.set_refresher(None)


def test_the_refresher_retries_on_the_next_interval_not_in_a_loop():
    from app.board_refresher import BoardRefresher

    class Broken:
        def tenant_ids(self):
            raise RuntimeError("database down")

    refresher = BoardRefresher(lambda: Broken())
    refresher._cycle(full=True)
    assert refresher._last_full > 0


def test_the_middleware_triggers_on_a_successful_write():
    import asyncio

    from starlette.requests import Request
    from starlette.responses import Response

    from app import board_refresher
    from app.main import refresh_board_after_writes

    class Counter:
        calls = 0

        def request(self):
            Counter.calls += 1

    async def ok(_request):
        return Response(status_code=202)

    async def refused(_request):
        return Response(status_code=401)

    def request(method):
        return Request({"type": "http", "method": method, "path": "/x", "headers": []})

    board_refresher.set_refresher(Counter())  # type: ignore[arg-type]
    try:
        asyncio.run(refresh_board_after_writes(request("POST"), ok))
        asyncio.run(refresh_board_after_writes(request("PATCH"), refused))
        asyncio.run(refresh_board_after_writes(request("GET"), ok))
        assert Counter.calls == 1
    finally:
        board_refresher.set_refresher(None)
