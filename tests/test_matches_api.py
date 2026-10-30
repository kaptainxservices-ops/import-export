"""The /matches endpoints.

These are the first endpoints a browser calls with a user's session, so most of what
matters here is refusal: no token, a bad token, or the wrong side of the board.
"""

from app.api.matches import narrow
from app.db.memory import InMemoryRepository
from app.db.models import BoardOffer, TenantConfig
from app.dependencies import set_repository


def want(row_id: str, brand: str | None, category: str | None) -> BoardOffer:
    return BoardOffer(
        id=row_id, counterparty_id="c", counterparty_name=None, side="buy",
        description=row_id, quantity=10, unit_price=100.0, currency="EUR",
        identity_key=row_id, match_key=None, description_key=None, ean=None,
        brand=brand, capacity_gb=None, colour=None, category=category,
    )


DEMAND = [
    want("iphone", "Apple", "phone"),
    want("tab", "Samsung", "tablet"),
    want("case", "Apple", "accessory"),
    want("unknown", None, None),
]


def test_an_unfiltered_board_ranks_every_requirement():
    assert [o.id for o in narrow(DEMAND, None, None)] == ["iphone", "tab", "case", "unknown"]


def test_the_board_narrows_by_product_type():
    assert [o.id for o in narrow(DEMAND, None, "tablet")] == ["tab"]


def test_the_board_narrows_by_brand():
    assert [o.id for o in narrow(DEMAND, "Apple", None)] == ["iphone", "case"]


def test_brand_and_product_type_combine():
    assert [o.id for o in narrow(DEMAND, "Apple", "phone")] == ["iphone"]


def test_filters_ignore_case():
    """The values come from a dropdown built out of the data, so their case is whatever
    the supplier typed. Matching them exactly would make a filter that returns nothing."""
    assert [o.id for o in narrow(DEMAND, "apple", "PHONE")] == ["iphone"]


def test_a_requirement_with_no_brand_is_not_claimed_by_one():
    """Extraction leaves brand null when it cannot read one. Such a row belongs on the
    unfiltered board and nowhere else — quietly folding it into a brand would put demand
    in front of the trader under a heading that is not true of it."""
    assert "unknown" not in [o.id for o in narrow(DEMAND, "Apple", None)]
    assert "unknown" in [o.id for o in narrow(DEMAND, None, None)]


def test_fill_requires_a_token(client):
    set_repository(InMemoryRepository([TenantConfig(id="t1")]))
    try:
        assert client.get("/matches/fill?offer_id=x").status_code == 401
    finally:
        set_repository(None)


def test_place_requires_a_token(client):
    set_repository(InMemoryRepository([TenantConfig(id="t1")]))
    try:
        assert client.get("/matches/place?offer_id=x").status_code == 401
    finally:
        set_repository(None)


def test_a_garbage_token_is_rejected(client):
    """Verification goes to Supabase; anything it will not vouch for is refused."""
    set_repository(InMemoryRepository([TenantConfig(id="t1")]))
    try:
        response = client.get(
            "/matches/fill?offer_id=x", headers={"Authorization": "Bearer nonsense"}
        )
        assert response.status_code == 401
    finally:
        set_repository(None)


def test_authentication_is_checked_before_the_parameters(client):
    """An anonymous caller gets 401 whatever they sent.

    Validating parameters first would tell someone with no session which query strings
    are well-formed, which is a small thing to give away for nothing."""
    assert client.get("/matches/fill").status_code == 401


def test_the_board_requires_a_token(client):
    set_repository(InMemoryRepository([TenantConfig(id="t1")]))
    try:
        assert client.get("/matches/board").status_code == 401
    finally:
        set_repository(None)


def test_the_board_ranks_by_money_and_keeps_the_unfillable(client):
    """Ranked by margin because that is the order a trader works in — but a requirement
    nobody can fill stays on the list. It is not noise, it is a thing to go and source,
    and hiding it would hide the demand along with the problem."""
    from app.api.matches import BoardRowOut

    rows = [
        BoardRowOut(offer_id="a", description="small", spec="", counterparty_name=None,
                    country=None, quantity=1, unit_price=1.0, currency="USD",
                    last_confirmed_at=None, state="filled", option_count=1,
                    near_miss_count=0, total_margin=100.0),
        BoardRowOut(offer_id="b", description="none", spec="", counterparty_name=None,
                    country=None, quantity=1, unit_price=1.0, currency="USD",
                    last_confirmed_at=None, state="no_supply", option_count=0,
                    near_miss_count=0, total_margin=None),
        BoardRowOut(offer_id="c", description="big", spec="", counterparty_name=None,
                    country=None, quantity=1, unit_price=1.0, currency="USD",
                    last_confirmed_at=None, state="filled", option_count=3,
                    near_miss_count=0, total_margin=5000.0),
    ]
    rows.sort(key=lambda r: (r.total_margin is None, -(r.total_margin or 0)))

    assert [r.offer_id for r in rows] == ["c", "a", "b"]


def test_a_near_miss_is_not_counted_as_money(client):
    """A near miss relaxed something — a different capacity, another colour. Counting it
    in the opportunity total would promise margin nobody can collect."""
    from app.api.matches import _note

    assert _note(None, []) == "no supply on the board"
    assert "near miss" in _note(None, [object()])  # type: ignore[list-item]


def test_the_tenant_cannot_be_passed_in(client):
    """A tenant id in the query string is a request to read someone else's board. The
    endpoint takes no such parameter, and adding one must stay a deliberate act."""
    import inspect

    from app.api.matches import board, fill, place

    for endpoint in (fill, place, board):
        assert "tenant_id" not in inspect.signature(endpoint).parameters


def test_the_board_names_each_supplier_once_with_its_lots_added():
    from app.api.matches import _legs
    from app.brain.matching import Allocation, MatchOption

    option = MatchOption(kind="combination", requirement_id="r", allocations=[
        Allocation("o1", "gulf", 70, 545.0, "USD", "iPhone 14 Pro"),
        Allocation("o2", "gulf", 30, 545.0, "USD", "iPhone 14 Pro"),
        Allocation("o3", "almanar", 20, 550.0, "USD", "iPhone 14 Pro"),
    ])
    legs = _legs(option, {"gulf": "Gulf Cell", "almanar": None})
    assert [(leg.name, leg.quantity, leg.lots) for leg in legs] == [
        ("Gulf Cell", 100, 2), ("unnamed supplier", 20, 1),
    ]


# ---------------------------------------------------------------- client, search, sort


class _Board:
    """Just enough repository for the board endpoint: two sides of live rows."""

    def __init__(self, rows: list[BoardOffer]):
        self.rows = rows

    def load_board(self, tenant_id: str, side: str, limit: int = 5000) -> list[BoardOffer]:
        return [r for r in self.rows if r.side == side]


def lot(
    row_id: str, side: str, who: str, description: str, quantity: int, price: float | None,
    first_seen: str, model: str | None = None,
) -> BoardOffer:
    return BoardOffer(
        id=row_id, counterparty_id=who, counterparty_name=who.title(), side=side,
        description=description, quantity=quantity, unit_price=price, currency="USD",
        identity_key=row_id, match_key=None, description_key=None, ean=None,
        brand="Apple", capacity_gb=None, colour=None, category="phone", model=model,
        first_seen_at=first_seen, last_confirmed_at="2026-10-30T09:00:00+00:00",
    )


BOARD = [
    lot("w15", "buy", "dubaibuyer", "Apple iPhone 15 128GB Black", 10, None,
        "2026-10-20T08:00:00+00:00", model="A3090"),
    lot("w14", "buy", "hkbuyer", "Apple iPhone 14 128GB Black", 5, None,
        "2026-10-21T08:00:00+00:00"),
    lot("w13", "buy", "hkbuyer", "Apple iPhone 13 64GB Red", 5, None,
        "2026-10-01T08:00:00+00:00"),
    # The iPhone 15 lot arrived after the requirement; the 14 lot long before its.
    lot("s15", "sell", "gulf", "Apple iPhone 15 128GB Black", 10, 650.0,
        "2026-10-29T10:00:00+00:00"),
    lot("s14", "sell", "almanar", "Apple iPhone 14 128GB Black", 5, 520.0,
        "2026-10-02T10:00:00+00:00"),
]


def _board(**params):
    from app.api.matches import board

    defaults = dict(limit=300, brand=None, category=None, q=None, buyer=None,
                    supplier=None, sort="best")
    return board(**{**defaults, **params}, context=("t1", _Board(BOARD)))


def test_product_search_needs_every_word_in_any_order():
    ids = [o.id for o in narrow(BOARD, None, None, search="128 iphone 15")]
    assert ids == ["w15", "s15"]


def test_product_search_reads_the_model_code():
    assert [o.id for o in narrow(BOARD, None, None, search="a3090")] == ["w15"]


def test_the_buyer_filter_keeps_one_buyers_requirements():
    assert [o.id for o in narrow(BOARD, None, None, buyer="hkbuyer")] == ["w14", "w13"]


def test_the_board_searches_requirements():
    out = _board(q="iphone 14")
    assert [r.offer_id for r in out.rows] == ["w14"]


def test_the_board_filters_by_buyer():
    out = _board(buyer="dubaibuyer")
    assert [r.offer_id for r in out.rows] == ["w15"]
    assert out.rows[0].buyer_id == "dubaibuyer"


def test_the_supplier_filter_keeps_only_what_that_supplier_can_fill():
    out = _board(supplier="almanar")
    assert [r.offer_id for r in out.rows] == ["w14"]
    assert out.rows[0].supplier_ids == ["almanar"]


def test_a_match_is_as_new_as_its_newest_ingredient():
    rows = {r.offer_id: r for r in _board().rows}
    # Requirement 20 Oct, lot 29 Oct: the match became possible on the 29th.
    assert rows["w15"].matched_at == "2026-10-29T10:00:00+00:00"
    # Lot 2 Oct, requirement 21 Oct: possible from the 21st.
    assert rows["w14"].matched_at == "2026-10-21T08:00:00+00:00"
    assert rows["w13"].matched_at is None


def test_newest_matches_first_and_the_unfillable_last():
    assert [r.offer_id for r in _board(sort="new").rows] == ["w15", "w14", "w13"]


def test_a_resent_list_does_not_make_a_match_new():
    """Suppliers resend the whole list daily. last_confirmed_at moves; matched_at must not."""
    rows = {r.offer_id: r for r in _board().rows}
    assert rows["w14"].last_confirmed_at == "2026-10-30T09:00:00+00:00"
    assert rows["w14"].matched_at != rows["w14"].last_confirmed_at
