"""Is this email offering stock, or asking for it?

Getting this backwards is not a cosmetic error: a supplier's price list filed as buyer
requirements would have the client chasing people to sell them stock those people are
themselves selling.

The trade signals its intent plainly and consistently — WTS and WTB are used as
headline words across the sample, in every language — so this is deterministic. A model
call is the fallback for the genuinely ambiguous minority, not the default, and until
the Anthropic key is wired in this module is the whole answer.

`side=None` means undecided, and undecided must not be filed as either. An unattributed
email in a review queue costs a click; a supplier's catalogue filed as buyer demand
corrupts the board.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Side = Literal["sell", "buy"]

_BUY_SIGNALS = re.compile(
    r"\b(?:wtb|want\s*to\s*buy|we\s*(?:want|need|are\s*looking)\s*to\s*buy|"
    r"buying|we\s*buy|purchase\s*(?:request|list)|"
    r"request\s*(?:for\s*)?(?:offer|quotation|price)|rfq|"
    r"send\s*(?:us\s*)?your\s*(?:best\s*)?(?:offer|price)|"
    r"looking\s*for|suche|ankauf|einkauf|zoeken|poszukuj)\b",
    re.IGNORECASE,
)

_SELL_SIGNALS = re.compile(
    r"\b(?:wts|want\s*to\s*sell|we\s*sell|for\s*sale|selling|"
    r"price\s*list|pricelist|preisliste|prijslijst|listino|lista\s*de\s*precios|"
    r"stock\s*(?:list|sheet|offer)|lagerliste|lagerbestand|"
    r"our\s*(?:offer|stock|availability)|available\s*stock|verkauf|angebot)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Classification:
    side: Side | None
    confidence: float
    reason: str
    needs_llm: bool = False


def classify_side(subject: str | None, body: str | None) -> Classification:
    """Decide whether an email is a seller offer or a buyer request.

    The subject carries far more weight than the body. Suppliers title their emails
    'WTS Pricelist 24.07' and buyers title theirs 'WTB Action'; the bodies of both are
    full of product names and prices, and a signature block mentioning 'we buy and
    sell' appears in plenty of each.
    """
    subject = subject or ""
    body = body or ""

    subject_buy = bool(_BUY_SIGNALS.search(subject))
    subject_sell = bool(_SELL_SIGNALS.search(subject))

    if subject_buy and not subject_sell:
        return Classification("buy", 0.95, f"subject says {_first(_BUY_SIGNALS, subject)!r}")
    if subject_sell and not subject_buy:
        return Classification("sell", 0.95, f"subject says {_first(_SELL_SIGNALS, subject)!r}")

    # Both in the subject: 'WTS / WTB list' happens. The body decides, weakly.
    head = body[:2000]
    body_buy = len(_BUY_SIGNALS.findall(head))
    body_sell = len(_SELL_SIGNALS.findall(head))

    if subject_buy and subject_sell:
        if body_buy > body_sell:
            return Classification("buy", 0.6, "subject says both; body leans buy", True)
        if body_sell > body_buy:
            return Classification("sell", 0.6, "subject says both; body leans sell", True)
        return Classification(None, 0.0, "subject says both and body is balanced", True)

    if body_buy and not body_sell:
        return Classification("buy", 0.7, f"body says {_first(_BUY_SIGNALS, head)!r}")
    if body_sell and not body_buy:
        return Classification("sell", 0.7, f"body says {_first(_SELL_SIGNALS, head)!r}")
    if body_sell > body_buy:
        return Classification("sell", 0.6, "body leans sell", True)
    if body_buy > body_sell:
        return Classification("buy", 0.6, "body leans buy", True)

    return Classification(None, 0.0, "no buy or sell wording found", True)


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0) if match else ""
