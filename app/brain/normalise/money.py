"""Currency, price, price basis, incoterm, VAT.

The single most dangerous field in the system is a bare '$'. It is USD in Dubai, AED
nowhere, SGD in Singapore, CAD in Toronto and AUD in Sydney — and a currency error
does not look like an error. It looks like an unusually good margin, which is exactly
the kind of number a broker acts on quickly.

So `normalise_currency` refuses to resolve a bare symbol on its own. It takes a
`default` argument, which the caller supplies from that counterparty's configured
default currency — a fact the client sets once per supplier, rather than a guess the
parser makes 500 times a day.
"""

import re

_SYMBOLS: dict[str, str] = {
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₽": "RUB",
    "د.إ": "AED",
    "₩": "KRW",
}

_CODES = {
    "USD", "AED", "EUR", "GBP", "HKD", "SGD", "JPY", "CNY", "INR",
    "AUD", "CAD", "CHF", "SAR", "QAR", "KWD", "BRL", "ZAR", "TRY", "KRW", "RUB",
}

# Words traders write instead of a code.
_WORDS = {
    "usd": "USD", "dollars": "USD", "dollar": "USD", "us$": "USD", "usd$": "USD",
    "dirham": "AED", "dirhams": "AED", "aed": "AED", "dhs": "AED", "dh": "AED",
    "euro": "EUR", "euros": "EUR",
    "pound": "GBP", "pounds": "GBP", "sterling": "GBP", "gbp": "GBP",
    "yuan": "CNY", "rmb": "CNY",
    "rupee": "INR", "rupees": "INR", "inr": "INR",
    "yen": "JPY",
}

_AMBIGUOUS_SYMBOL = re.compile(r"[$]")

_PRICE = re.compile(
    r"(?<![\d.])(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)(?![\d])"
)

_INCOTERMS = ["EXW", "FCA", "FOB", "CIF", "CFR", "CPT", "CIP", "DAP", "DPU", "DDP", "DDU"]

_PER_LOT = re.compile(
    r"\b(?:per\s*lot|for\s*the\s*lot|lot\s*price|total\s*price|whole\s*lot|job\s*lot)\b",
    re.IGNORECASE,
)
_PER_UNIT = re.compile(
    r"\b(?:per\s*(?:unit|pc|pcs|piece|pieces|ea|each|handset|phone)|/\s*(?:pc|pcs|unit|ea)|ea\b|each\b)",
    re.IGNORECASE,
)

# No surrounding \b on these: a word boundary cannot sit between a space and a '+',
# so '905 + VAT' would never match a \b-wrapped alternation.
_VAT_EXCLUDED = re.compile(
    r"(?:excl?\w*\.?\s*(?:of\s+)?vat|vat\s+excl\w*|\bex\s+vat|\bno\s+vat|\bplus\s+vat|\+\s*vat)",
    re.IGNORECASE,
)
_VAT_INCLUDED = re.compile(
    r"(?:incl?\w*\.?\s*(?:of\s+)?vat|vat\s+incl\w*|\bwith\s+vat)",
    re.IGNORECASE,
)


def normalise_currency(raw: str | None, default: str | None = None) -> str | None:
    """Resolve a currency to an ISO 4217 code.

    `default` is that counterparty's configured currency and is used only to resolve an
    ambiguous bare '$'. An explicit code in the text always wins over the default —
    a supplier who normally quotes AED but wrote 'USD' today meant USD.
    """
    if not raw:
        return None

    text = raw.strip()
    upper = text.upper()

    for code in _CODES:
        if re.search(rf"\b{code}\b", upper):
            return code

    for symbol, code in _SYMBOLS.items():
        if symbol in text:
            return code

    lowered = text.lower()
    for word, code in _WORDS.items():
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            return code

    if _AMBIGUOUS_SYMBOL.search(text):
        # Deliberately not defaulting to USD. Without a configured default this row
        # goes to review, which costs seconds; a wrong currency costs a deal.
        return default

    return None


def parse_price(raw: str | None) -> float | None:
    """Extract a price. Handles '1,234.50', '905', 'USD 905.00', '$ 905'."""
    if not raw:
        return None

    match = _PRICE.search(raw.replace(" ", ""))
    if not match:
        return None

    try:
        return float(match.group("n").replace(",", ""))
    except ValueError:
        return None


def detect_price_basis(raw: str | None) -> str:
    """'per_unit', 'per_lot' or 'unknown'.

    Returns 'unknown' rather than assuming per-unit. A lot price read as a unit price
    inflates an apparent margin by the quantity of the lot — a hundredfold error on a
    hundred-unit lot, and one that looks like the deal of the year.
    """
    if not raw:
        return "unknown"

    if _PER_LOT.search(raw):
        return "per_lot"
    if _PER_UNIT.search(raw):
        return "per_unit"
    return "unknown"


def detect_incoterm(raw: str | None) -> str | None:
    if not raw:
        return None

    upper = raw.upper()
    for term in _INCOTERMS:
        if re.search(rf"\b{term}\b", upper):
            return term
    return None


def detect_vat_included(raw: str | None) -> bool | None:
    """True, False, or None when the text does not say. None is common and correct.

    Excluded is tested first. '+ VAT' and 'ex VAT' are the phrasings a seller uses when
    the number quoted is the smaller one, and reading those as VAT-inclusive would
    understate the real cost by the VAT rate on every unit.
    """
    if not raw:
        return None

    lowered = raw.lower()
    if _VAT_EXCLUDED.search(lowered):
        return False
    if _VAT_INCLUDED.search(lowered):
        return True
    return None
