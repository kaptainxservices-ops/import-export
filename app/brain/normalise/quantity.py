"""Quantity: '40pcs', 'x40', 'qty: 40', '40 units' -> 40.

Quantity is checked against a ceiling before it is believed. A parser that reads '256'
out of 'iPhone 15 256GB' as a quantity turns a 40-unit lot into a 256-unit one, and
combination matching then promises a buyer stock that does not exist.
"""

import re

_MAX_PLAUSIBLE = 100_000

_PATTERNS = [
    re.compile(r"\bqty\.?\s*[:=]?\s*(?P<n>\d{1,6})\b", re.IGNORECASE),
    re.compile(r"\bquantity\s*[:=]?\s*(?P<n>\d{1,6})\b", re.IGNORECASE),
    re.compile(r"\b(?P<n>\d{1,6})\s*(?:pcs?|pieces?|units?|handsets?|nos?)\b", re.IGNORECASE),
    re.compile(r"\b(?P<n>\d{1,6})\s*[x×]\s*(?=[a-z])", re.IGNORECASE),   # '40x iPhone'
    re.compile(r"(?:^|\s)[x×]\s*(?P<n>\d{1,6})\b", re.IGNORECASE),        # 'iPhone x40'
]

# Storage sizes are stripped first so they can never be read as a count.
_STORAGE = re.compile(r"\b\d{1,4}\s*(?:gb|tb)\b", re.IGNORECASE)


def parse_quantity(raw: str | None) -> int | None:
    if not raw:
        return None

    text = _STORAGE.sub(" ", raw)

    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            value = int(match.group("n"))
            if 0 < value <= _MAX_PLAUSIBLE:
                return value

    # A cell containing nothing but a number is a quantity in a table column.
    stripped = text.strip()
    if re.fullmatch(r"\d{1,6}", stripped):
        value = int(stripped)
        if 0 < value <= _MAX_PLAUSIBLE:
            return value

    return None
