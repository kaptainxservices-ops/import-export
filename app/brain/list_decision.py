"""What a list does to the board: replace, add, refresh, or nothing.

One place for the decision, so it can be read, tested, and explained. Every list a
sender emails gets exactly one of these:

- **replace**  It is their whole list in these categories. New lines go on, changed
  prices and quantities are updated, and lines missing from it are closed as sold.
- **add**      It is extra stock, an order, or a second list today. New lines go on and
  changes are made; nothing is closed. Whatever sold closes with their next whole list.
- **add_new_only**  An email processed after a newer list from the same sender the
  same day (or a newer addition) already reached the board. Only lines the board has
  never had go on; nothing it says about existing lines is believed over what came
  after it.
- **refresh**  Identical to a list already applied today. Timestamps only.
- **reject**   Nothing it says is newer than the board: a whole list from the same
  sender on a later day already covered every category in it. Recorded, applied to nothing, and
  nobody is asked about it.

(Emails nothing could be read from, and whole lists that arrive suspiciously short, are
the other rejects; reconcile() refuses those and they go to Review.)

**The rules decide. The checker only checks.** When the rules lean towards replace but
are not sure -- the wording points both ways, there is no wording at all, or the list
would close a large share of the sender's live lines -- a model reads the email and is
asked one question: whole list, or addition? It can confirm the replace or turn it into
an add. It can never turn an add into a replace, so it can stop a closure and never
cause one. With no model configured, or one that is down, the rules' answer stands
except where closing is risky, where the list adds instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from app.brain.completeness import COMPLETE_SIGNALS, PARTIAL_SIGNALS, TRADE_PREFIX_ONLY
from app.brain.sameday import local_day

ListAction = Literal["replace", "add", "add_new_only", "refresh", "reject"]

# A replace that would close at least this many lines AND this share of what the sender
# has live in the list's categories is checked before it is applied, whatever the
# wording. Real senders sell a few lines a day; half the board vanishing at once is
# more often a list misread than a list sold.
HEAVY_CLOSE_LINES = 10
HEAVY_CLOSE_SHARE = 0.4


class AppliedList(Protocol):
    """An earlier list from the same sender and side that reached the board."""

    received_at: datetime
    is_complete_list: bool | None
    categories: list[str] | None


@dataclass(frozen=True)
class Wording:
    """What the covering words say about the list, before any counting."""

    whole: str | None = None          # 'price list', 'stock list', ...
    trade_prefix: str | None = None   # only 'WTS' / 'WTB', which head orders and lists alike
    extra: str | None = None          # 'just arrived', 'additional', ...

    @property
    def says_whole(self) -> bool:
        return bool(self.whole or self.trade_prefix)

    @property
    def conflict(self) -> bool:
        return self.says_whole and bool(self.extra)

    @property
    def silent(self) -> bool:
        return not self.says_whole and not self.extra


def read_wording(subject: str | None, body: str | None) -> Wording:
    text = f"{subject or ''}\n{body or ''}"
    whole = prefix = None
    for found in COMPLETE_SIGNALS.finditer(text):
        word = found.group(0)
        if TRADE_PREFIX_ONLY.match(word):
            prefix = prefix or word
        else:
            whole = whole or word
    extra = PARTIAL_SIGNALS.search(text)
    return Wording(whole, prefix, extra.group(0) if extra else None)


@dataclass(frozen=True)
class ListDecision:
    action: ListAction
    # Passed to reconcile(): True licenses closures, anything else forbids them.
    is_complete: bool | None
    reasons: tuple[str, ...]
    # Who decided: 'rules', or the rules plus what the checker said.
    decided_by: str = "rules"

    @property
    def closes(self) -> bool:
        return self.action == "replace"

    @property
    def note(self) -> str:
        """The decision as stored on the import, readable as it stands and parseable
        back (see parse_note): the import's other notes follow after NOTE_END."""
        return (
            f"decision: {self.action} ({self.decided_by}) -- "
            + REASON_SEP.join(self.reasons) + NOTE_END
        )


REASON_SEP = " · "
NOTE_END = " ||"


@dataclass(frozen=True)
class StoredDecision:
    action: str
    decided_by: str
    reasons: tuple[str, ...]
    rest: str


def parse_note(notes: str | None) -> StoredDecision | None:
    """Read a decision back off an import's notes. None for imports from before it."""
    text = notes or ""
    if not text.startswith("decision: ") or NOTE_END not in text:
        return None
    head, _, rest = text.partition(NOTE_END)
    label, _, reasons = head[len("decision: "):].partition(" -- ")
    action, _, by = label.partition(" (")
    return StoredDecision(
        action=action.strip(),
        decided_by=by.rstrip(")").strip() or "rules",
        reasons=tuple(r for r in reasons.split(REASON_SEP) if r),
        rest=rest.lstrip("; ").strip(),
    )


# ---------------------------------------------------------------- late emails


@dataclass(frozen=True)
class Lateness:
    """What newer lists from the same sender already said about this one's categories."""

    # Categories (None for uncategorised) a whole list from a LATER DAY already covered.
    # This email's lines there are a day or more out of date and are dropped.
    superseded: frozenset[str | None] = frozenset()
    # A newer list the same day (or a newer addition on a later day) touched the same
    # categories. This email may still add lines the board has never had, but must not
    # overwrite or close anything.
    behind: bool = False
    newest: datetime | None = None

    @property
    def late(self) -> bool:
        return bool(self.superseded) or self.behind


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)  # noqa: UP017


def lateness(
    applied: Iterable[AppliedList],
    arriving: datetime,
    habits: set[str | None],
    zone: str = "Europe/Amsterdam",
) -> Lateness:
    """Compare an arriving email against lists from the same sender that arrived AFTER it.

    The case: n8n is down for an evening, catches up the next morning, and Monday's list
    is processed after Tuesday's. Read as the first list of Monday it replaced Tuesday's
    board -- closing everything Tuesday added and putting Monday's prices back.

    Only a whole list from a later DAY supersedes. Two lists minutes apart on one
    morning -- 'Today's Apple Watch offer' at 07:45 and 'Today's Apple Accessories
    offer' at 07:48 -- are both today's stock; processed in the wrong order, the older
    one still puts its new lines on, it just may not overwrite or close anything.

    `habits` are the categories this email is about ({None} for a sender who never
    categorises). A newer list recorded before categories were stored (categories None)
    is taken to have covered everything.
    """
    arriving = _aware(arriving)
    that_day = local_day(arriving, zone)
    superseded: set[str | None] = set()
    behind = False
    newest: datetime | None = None
    for listed in applied:
        when = _aware(listed.received_at)
        if when <= arriving:
            continue
        covered = (
            set(habits) if listed.categories is None
            else {c or None for c in listed.categories} & habits
        )
        if not covered:
            continue
        newest = when if newest is None or when > newest else newest
        if listed.is_complete_list is True and local_day(when, zone) != that_day:
            superseded |= covered
        else:
            behind = True
    return Lateness(frozenset(superseded), behind, newest)


def keeps_row(category: str | None, habits: set[str | None], superseded: frozenset) -> bool:
    """Whether a row of a late email survives what newer lists already said.

    A row in one of the email's habitual categories goes with that category. A stray --
    one adapter in a phone list, or a row with no category -- goes with the email: it is
    dropped only when everything the email was about has been superseded.
    """
    if category in habits:
        return category not in superseded
    return not (habits <= superseded)


# ---------------------------------------------------------------- replace or add


Checker = Callable[[int], Literal["complete", "addition"] | None]


def settle(
    *,
    rule_complete: bool | None,
    rule_why: str,
    wording: Wording,
    would_close: int,
    live_in_scope: int,
    check: Checker | None = None,
) -> ListDecision:
    """Replace or add, with the checker asked only where the rules are unsure.

    `rule_complete` and `rule_why` are completeness.detect_list_completeness's answer.
    `would_close` is how many lines a replace would close (a dry run of reconcile).
    `check(would_close)` asks the model; None when there is no model to ask.
    """
    if rule_complete is not True:
        return ListDecision("add", rule_complete, (rule_why,))

    if would_close == 0:
        # Nothing to close: replace and add do the same thing, so there is nothing a
        # checker could change.
        return ListDecision("replace", True, (rule_why, "closes nothing"))

    heavy = (
        would_close >= HEAVY_CLOSE_LINES
        and live_in_scope > 0
        and would_close >= live_in_scope * HEAVY_CLOSE_SHARE
    )
    if wording.conflict:
        doubt = f"the wording points both ways ({wording.whole or wording.trade_prefix!r} " \
                f"and {wording.extra!r})"
    elif wording.silent:
        doubt = "no wording says whether it is the whole list"
    elif heavy:
        doubt = f"it would close {would_close} of {live_in_scope} live lines"
    else:
        return ListDecision(
            "replace", True, (rule_why, f"closes {would_close} missing lines as sold")
        )

    reading = check(would_close) if check else None
    if reading == "complete":
        return ListDecision(
            "replace", True,
            (rule_why, doubt, f"closes {would_close} missing lines as sold"),
            "rules, confirmed by the checker",
        )
    if reading == "addition":
        return ListDecision(
            "add", False,
            (rule_why, doubt, "the checker read the email as extra stock; closed nothing"),
            "rules, overruled by the checker",
        )

    unanswered = "checker unavailable" if check is None else "checker could not tell"
    if wording.conflict or heavy:
        return ListDecision(
            "add", None,
            (rule_why, doubt, f"{unanswered}; closed nothing until a list says so clearly"),
            f"rules; {unanswered}",
        )
    # A plain table with no covering words, at the size this sender always sends. The
    # client confirmed suppliers send their whole list every time, so the rules' reading
    # stands when nobody can contradict it.
    return ListDecision(
        "replace", True,
        (rule_why, doubt, f"{unanswered}; read as the whole list, as this sender's lists are",
         f"closes {would_close} missing lines as sold"),
        f"rules; {unanswered}",
    )
