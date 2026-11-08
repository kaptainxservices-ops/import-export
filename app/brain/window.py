"""How far back the match board looks.

Every row on the board is a claim about a third party's goods, and a claim nobody has
repeated in a week is not one to put money against. A supplier who still has the stock
lists it again -- most resend their whole list daily -- so `last_confirmed_at` moves
with every list that still carries the line. A requirement or a lot that has not
been confirmed inside the window is left out of matching altogether: not counted as a
live match, not counted as unmet demand, not offered as supply.

The offers themselves are untouched. They stay on Sellers and Buyers until a list closes
them; the window only decides what the matching engine is allowed to see.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

DEFAULT_DAYS = 7


class Confirmed(Protocol):
    last_confirmed_at: str | None


T = TypeVar("T", bound=Confirmed)


def window_days() -> int:
    from app.config import get_settings

    return get_settings().match_window_days or DEFAULT_DAYS


def window_start(now: datetime | None = None, days: int | None = None) -> datetime:
    """The oldest confirmation still inside the window."""
    now = now or datetime.now(UTC)
    return now - timedelta(days=days if days is not None else window_days())


def _parse(stamp: str | datetime) -> datetime | None:
    if isinstance(stamp, datetime):
        moment = stamp
    else:
        try:
            moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def in_window(offer: Confirmed, start: datetime) -> bool:
    """Confirmed at or after `start`. An offer with no confirmation time is kept: the
    database always sets one, so a missing stamp means a caller that does not track it,
    not stock that has gone quiet."""
    stamp = offer.last_confirmed_at
    if not stamp:
        return True
    moment = _parse(stamp)
    return moment is None or moment >= start


def recent(offers: Iterable[T], start: datetime | None = None) -> list[T]:
    """The offers confirmed inside the window."""
    start = start or window_start()
    return [o for o in offers if in_window(o, start)]
