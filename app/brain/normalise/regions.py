"""Region codes: 'LL/A', 'lla', 'US spec' -> 'LL/A'.

Region code is one of the largest price drivers in this trade — the same handset in
ZP/A and LL/A are different products commercially — so it is part of offer identity
and it is never inferred loosely.

The explicit code is trusted. A country name is only mapped where the trade uses it as
a direct synonym for a code, and anything else returns None rather than a guess.
Confusing a Japanese J/A unit (whose camera shutter cannot be silenced) with an LL/A
one is a real dispute with a real customer.
"""

import re

_CODES = {
    "LL": "LL/A",   # United States
    "ZP": "ZP/A",   # UAE / Middle East, Hong Kong
    "CH": "CH/A",   # mainland China
    "J": "J/A",     # Japan
    "X": "X/A",     # Australia / New Zealand
    "B": "B/A",     # United Kingdom / Ireland
    "F": "F/A",     # France
    "D": "D/A",     # Germany
    "T": "T/A",     # Italy
    "Y": "Y/A",     # Spain
    "ZA": "ZA/A",   # Singapore
    "ZD": "ZD/A",   # Europe (multi-country)
    "IP": "IP/A",   # India
    "KH": "KH/A",   # South Korea
    "TA": "TA/A",   # Taiwan
    "AA": "AA/A",   # Canada
    "VC": "VC/A",   # Canada (alternate)
    "HN": "HN/A",   # India (alternate)
    "RS": "RS/A",   # Russia
    "BR": "BR/A",   # Brazil
    "HX": "HX/A",   # India (newer)
    "QN": "QN/A",   # Nordics
}

# Which market each code is built for. Matching compares these rather than the codes,
# so a buyer asking for 'INDIA SPEC' meets HN/A stock and is not offered ZD/A stock --
# both of which a plain string comparison got wrong, one each way.
_FAMILY = {
    "LL/A": "US", "US": "US", "ZP/A": "ME", "CH/A": "CN", "J/A": "JP", "X/A": "AU",
    "B/A": "UK", "UK": "UK", "F/A": "EU", "D/A": "EU", "T/A": "EU", "Y/A": "EU",
    "ZD/A": "EU", "QN/A": "EU", "EU": "EU", "ZA/A": "SG", "IP/A": "INDIA", "HN/A": "INDIA",
    "HX/A": "INDIA", "INDIA": "INDIA", "KH/A": "KR", "TA/A": "TW", "AA/A": "CA",
    "VC/A": "CA", "RS/A": "RU", "BR/A": "BR", "NON-EU": "NON-EU", "GLOBAL": "GLOBAL",
}


def region_family(code: str | None) -> str | None:
    """The market a region code belongs to: 'ZD/A' and 'EU' are both EU."""
    if not code:
        return None
    return _FAMILY.get(code.upper(), code.upper())


def regions_conflict(wanted: str | None, offered: str | None) -> bool:
    """Whether a lot's market rules it out for a buyer. Unknown on either side is not a
    conflict, and NON-EU stock is any market except the EU."""
    a, b = region_family(wanted), region_family(offered)
    if not a or not b or a == b:
        return False
    if "NON-EU" in (a, b):
        return "EU" in (a, b)
    return True

# The vocabulary these suppliers actually use. The Apple /A part codes appear in the
# original specification but nowhere in the real sample; what the lists genuinely
# distinguish is EU versus non-EU, and India as a separate market. Both sets are kept:
# the /A codes cost nothing and may appear from other suppliers.
_MARKET_PHRASES = {
    "non eu": "NON-EU",
    "non-eu": "NON-EU",
    "noneu": "NON-EU",
    "india spec": "INDIA",
    "indian spec": "INDIA",
    "eu spec": "EU",
    "euro spec": "EU",
    "european spec": "EU",
}

# Only phrasings the trade uses as a direct synonym for an Apple part code.
_PHRASES = {
    "us spec": "LL/A",
    "usa spec": "LL/A",
    "us version": "LL/A",
    "american spec": "LL/A",
    "uae spec": "ZP/A",
    "middle east spec": "ZP/A",
    "me spec": "ZP/A",
    "hk spec": "ZP/A",
    "china spec": "CH/A",
    "chinese spec": "CH/A",
    "japan spec": "J/A",
    "japanese spec": "J/A",
    "uk spec": "B/A",
    "india spec": "IP/A",
    "indian spec": "IP/A",
    "singapore spec": "ZA/A",
    "korea spec": "KH/A",
}

_CODE_PATTERN = re.compile(r"\b([A-Z]{1,2})\s*/\s*A\b", re.IGNORECASE)
# The region inside an Apple part number: MFYP4ZD/A is a European unit, MTP03HX/A an
# Indian one. Five characters of model, then the market, then '/A'.
_PART_NUMBER = re.compile(r"\bM[A-Z0-9]{4}([A-Z]{1,2})/A\b", re.IGNORECASE)
# 'LLA' with no slash, as typed in spreadsheets.
_SQUASHED = re.compile(r"\b([A-Z]{1,2})A\b")


def normalise_region_code(raw: str | None) -> str | None:
    """Canonical market or Apple region code, or None when the text does not state one.

    A warehouse location is not a market. 'Shipping from Hong Kong' says where the
    goods sit, not which market they are built for, and treating one as the other is a
    real customer dispute — a Japanese unit whose camera shutter cannot be silenced is
    not an EU unit.
    """
    if not raw:
        return None

    text = re.sub(r"\s+", " ", raw).strip()
    if not text:
        return None

    lowered_all = text.lower()
    # 'NON EU' must be tested before 'EU', or every non-EU line reads as EU.
    for phrase in sorted(_MARKET_PHRASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered_all):
            return _MARKET_PHRASES[phrase]

    if re.search(r"(?<![a-z])non[\s-]?eu(?![a-z])", lowered_all):
        return "NON-EU"
    if re.search(r"(?<![a-z])eu(?![a-z])", lowered_all):
        return "EU"

    # Only codes Apple actually uses. 'A/A+' is a grade, not a market, and reading
    # every 'X/A' as a region turned it into region 'A/A' -- harmless while region sat
    # outside identity, a phantom second lot once it is part of it.
    match = _CODE_PATTERN.search(text)
    if match and match.group(1).upper() in _CODES:
        return _CODES[match.group(1).upper()]

    part = _PART_NUMBER.search(text)
    if part and part.group(1).upper() in _CODES:
        return _CODES[part.group(1).upper()]

    lowered = text.lower()
    for phrase, code in _PHRASES.items():
        if phrase in lowered:
            return code

    squashed = _SQUASHED.search(text.upper())
    if squashed and squashed.group(1) in _CODES:
        return _CODES[squashed.group(1)]

    # A bare code with no /A and no context, e.g. a spreadsheet column of 'LL', 'ZP'.
    bare = text.upper()
    if bare in _CODES:
        return _CODES[bare]

    return None
