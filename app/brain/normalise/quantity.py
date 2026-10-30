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
    # '... (MXP63LL/A) 200x 95,- EUR': the count after the name, before the price.
    # Never '2x2' (an antenna spec) or '4x4': the x must stand alone.
    re.compile(r"\b(?P<n>\d{1,6})\s?[x×](?=\s|,|;|$)", re.IGNORECASE),
    # 'iPhone x40' -- but never a model code. 'X130 128GB' and 'Moto X30 Pro' are
    # phones, and this read them as 130 and 30 units. A count written this way is a
    # lower-case x, or a capital X at the very end of the line.
    re.compile(r"(?:^|\s)(?:x|×)\s*(?P<n>\d{1,6})\b"),
    re.compile(r"\sX\s*(?P<n>\d{1,6})\s*[.,;!]?\s*$"),
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

    # The cell as written, not with its storage size removed: 'X130 128GB' minus the
    # 128GB is 'X130', which is a model and not a hundred and thirty units.
    value = _whole_cell(raw.strip())
    if value is not None and 0 < value <= _MAX_PLAUSIBLE:
        return value

    return None


# Whole-cell shapes only. Each is unambiguous when it is all a cell holds, and none is
# safe to look for inside a sentence: '1k' in 'iPhone 1k' or '50+' in 'Galaxy S50+'
# would read a model as a count.
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,\u00a0 ]\d{3})+")            # '1,000' '1.000' '1 000'
_KILO = re.compile(r"(\d{1,3}(?:[.,]\d{1,3})?)\s*k", re.IGNORECASE)  # '1k' '2.5k' '1,5K'
_TIMES = re.compile(r"(?:[x×]\s*(\d{1,6})|(\d{1,6})\s*[x×])", re.IGNORECASE)  # '200x' 'x200'
_AT_LEAST = re.compile(r"(?:>\s*|min\.?\s*)?(\d{1,6})\s*\+?")      # '50+' '>50' 'min 50'


def _whole_cell(cell: str) -> int | None:
    """Quantities buyers and suppliers actually write in a quantity column.

    Phoneport's wanted list is '200x', '400x', '1k' throughout: read as nothing, all 44
    requirements were dropped, since a wanted line has no price either.
    """
    if not cell:
        return None
    if _THOUSANDS.fullmatch(cell):
        return int(re.sub(r"\D", "", cell))
    match = _KILO.fullmatch(cell)
    if match:
        return round(float(match.group(1).replace(",", ".")) * 1000)
    match = _TIMES.fullmatch(cell)
    if match:
        return int(match.group(1) or match.group(2))
    match = _AT_LEAST.fullmatch(cell)
    if match and ("+" in cell or ">" in cell or "min" in cell.lower()):
        return int(match.group(1))
    return None
