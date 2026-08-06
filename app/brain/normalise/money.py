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

_NUMERIC = re.compile(r"\d[\d.,\s ]*\d|\d")

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


def parse_price(raw: str | None, decimal_hint: str | None = None) -> float | None:
    """Extract a price, handling both Anglo and European number formats.

    Both conventions appear in the same inbox, often in the same week, so this cannot
    be a global setting. The separator role is decided per value:

        1,234.50   both separators -> the rightmost is the decimal    -> 1234.50
        109,50     one separator, 2 digits after   -> decimal         -> 109.50
        1.079      one separator, 3 digits after   -> thousands       -> 1079.00
        12,500     one separator, 3 digits after   -> thousands       -> 12500.00
        15.4705199 one separator, 4+ digits after  -> decimal         -> 15.47

    The three-digit case is the genuinely ambiguous one — '1,079' could be 1079 or
    1.079 — and it is read as thousands, because a four-figure price is far more
    plausible in this trade than a one-euro phone. `decimal_hint` ('comma' or 'dot')
    overrides that when a supplier's convention is known.

    This matters more than it looks: reading €1,079 as €1.07 does not present as a
    parsing failure. It presents as an extraordinary margin, which is exactly the kind
    of number a broker acts on before checking.
    """
    if not raw:
        return None

    match = _NUMERIC.search(raw)
    if not match:
        return None

    token = re.sub(r"[\s ]", "", match.group(0)).strip(".,")
    if not token or not any(c.isdigit() for c in token):
        return None

    value = _interpret(token, decimal_hint)
    return value


def _interpret(token: str, decimal_hint: str | None) -> float | None:
    has_dot = "." in token
    has_comma = "," in token

    if not has_dot and not has_comma:
        return _to_float(token)

    if has_dot and has_comma:
        # Whichever appears last is the decimal point; the other groups thousands.
        decimal_sep = "." if token.rfind(".") > token.rfind(",") else ","
        return _split_on(token, decimal_sep)

    sep = "." if has_dot else ","

    if decimal_hint == "comma":
        return _split_on(token, ",") if has_comma else _to_float(token.replace(".", ""))
    if decimal_hint == "dot":
        return _split_on(token, ".") if has_dot else _to_float(token.replace(",", ""))

    if token.count(sep) > 1:
        # '1.234.567' — repeated separators can only be grouping.
        return _to_float(token.replace(sep, ""))

    tail = token.split(sep)[1]
    if len(tail) == 3:
        return _to_float(token.replace(sep, ""))
    return _split_on(token, sep)


def _split_on(token: str, decimal_sep: str) -> float | None:
    other = "," if decimal_sep == "." else "."
    return _to_float(token.replace(other, "").replace(decimal_sep, "."))


def _to_float(text: str) -> float | None:
    try:
        return float(text)
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
