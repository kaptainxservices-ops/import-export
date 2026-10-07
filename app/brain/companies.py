"""Recognising a supplier by the name in a subject line.

About one email in five arrives with no address anywhere in it: offers a staff member
retyped out of WhatsApp, or pasted from a chat. `resolve_sender` finds the company in
the subject — 'Vadimpex Offer' — and correctly refuses to invent an address for it, so
the email goes to a person.

That is right the first time and wrong the twentieth. Once somebody has said Vadimpex is
`sales@vadimpex.example`, asking again every morning is not caution, it is a tax. The
counterparty lookup only ever matched on email address, so the answer was never reused.

Matching on a name is riskier than matching on an address, and the cost is asymmetric:
filing a supplier's catalogue against the wrong counterparty corrupts two boards at
once, while sending one more email to the review queue costs a click. So the rule is
deliberately strict — one unambiguous match or nothing:

  * the name must be distinctive (a counterparty called 'Team' matches nothing)
  * legal suffixes are ignored, because 'Erregame' and 'Erregame SpA' are one company
  * a subject name may be part of a longer stored name, since display names arrive as
    'Inesa Zuber Smalltronic'
  * if two counterparties match, that is ambiguity, and ambiguity goes to a person
"""

from __future__ import annotations

import re

# Dropped before comparing. A supplier filed as 'Erregame' and signing as 'Erregame
# S.p.A.' is one company, and the suffix is the least informative part of the name.
_LEGAL = {
    "ab", "ag", "aps", "as", "bv", "co", "corp", "doo", "eood", "gmbh", "inc",
    "kft", "kg", "llc", "ltd", "limited", "nv", "ood", "ou", "oy", "plc", "sa",
    "sarl", "sas", "sl", "spa", "spzoo", "sro", "srl", "sp", "zoo", "ug",
}

# Words that identify nobody. These turn up as display names on shared mailboxes
# ('Team', 'Sales') and as whole subjects, and matching on them would attribute a
# stranger's price list to whichever supplier happened to be called Sales.
_GENERIC = {
    "team", "sales", "info", "offer", "offers", "request", "stock", "list",
    "trading", "trade", "export", "import", "group", "international", "telecom",
    "electronics", "mobile", "mobiles", "phone", "phones", "gsm", "distribution",
    "company", "the", "and", "new", "all", "best", "global", "world", "tech",
}

_PUNCTUATION = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE = re.compile(r"\s+")

# Below this a token is an abbreviation that could be anyone: 'OT', 'AB', 'SA'.
# Shorter tokens may still take part in a match, they just cannot carry one alone.
#
# Three rather than four, because the trade is full of real three-letter names -- VNN,
# AMD, GSM -- and excluding them sent a supplier to the review queue every morning
# forever. Two is where it stops: a two-letter token is as likely to be a country code
# or a legal form as a company.
_DISTINCTIVE_LENGTH = 3


def tokens(name: str | None) -> list[str]:
    """A company name reduced to comparable words."""
    if not name:
        return []
    flat = _PUNCTUATION.sub(" ", name.casefold())
    flat = _SPACE.sub(" ", flat).strip()
    # Single letters are dropped as well as legal forms. 'Erregame S.p.A.' loses its
    # punctuation and arrives as four tokens, three of which are the suffix spelled out
    # one letter at a time — and a one-letter token never identified anybody anyway.
    return [word for word in flat.split(" ") if len(word) > 1 and word not in _LEGAL]


def is_distinctive(name: str | None) -> bool:
    """Whether a name identifies anybody at all.

    'Vadimpex' does. 'Team' does not, and neither does 'International Trading'. A name
    made only of words that describe half the trade is not a name.
    """
    words = tokens(name)
    if not words:
        return False
    return any(len(word) >= _DISTINCTIVE_LENGTH and word not in _GENERIC for word in words)


def matches(subject_company: str | None, counterparty_name: str | None) -> bool:
    """Whether a subject line's company is this counterparty.

    True when every word of the subject name appears in the stored name. That direction
    matters and the other one is wrong: a display name is often longer than the company
    ('Inesa Zuber Smalltronic' for Smalltronic), so the subject name being contained in
    the stored name is the normal case. Reversed, the single word 'Trinity' in a stored
    name would swallow a subject reading 'Trinity Electronics Poland'.
    """
    wanted = tokens(subject_company)
    if not wanted or not is_distinctive(subject_company):
        return False

    have = set(tokens(counterparty_name))
    if not have:
        return False

    return all(word in have for word in wanted)


def find_unique(subject_company: str | None, candidates):
    """The one counterparty this name can only mean, or None.

    `candidates` is anything with `.name`. Two matches is not a near miss to be broken
    by picking the better one — it is the signal that the name is not enough, and the
    answer is a person.
    """
    if not is_distinctive(subject_company):
        return None

    found = [c for c in candidates if matches(subject_company, getattr(c, "name", None))]
    return found[0] if len(found) == 1 else None
