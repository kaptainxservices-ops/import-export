"""Model names: '15PM', 'iphone15 promax', 'IP 15 Pro Max' -> 'iPhone 15 Pro Max'.

This parses the *pattern* rather than looking names up in a table. A table would need
editing every September when Apple ships a new line, and the day it falls behind, an
entire new model silently stops matching and drops off the board. A parser handles the
iPhone 18 without a code change.
"""

import re

# Storage is stripped before the model number is read. Without this, 'iPhone 12 64GB'
# parses as an iPhone 64: both are two-digit numbers and the wrong one wins.
_STORAGE = re.compile(r"\b\d{1,4}\s*(?:gb|tb)\b", re.IGNORECASE)

_NOISE = re.compile(r"[®™,()\[\]]")

_FAMILY = r"(?:iphone|i\s?phone|ip)"
_HAS_FAMILY = re.compile(rf"{_FAMILY}\b", re.IGNORECASE)

# Other product lines. A supplier's list is not always only phones, and 'MacBook Pro 14'
# must not be filed as an iPhone 14 Pro — the number and the variant word are both
# there, so without this the parser is confidently wrong.
_NOT_IPHONE = re.compile(
    r"\b(?:macbook|imac|ipad|airpods?|airtag|watch|mac\s?mini|mac\s?studio|"
    r"samsung|galaxy|pixel|xiaomi|redmi|oneplus|oppo|vivo|huawei|realme|"
    r"tablet|laptop|charger|cable|case)\b",
    re.IGNORECASE,
)

# Longest variants first: 'pro max' must win before 'pro' can match it.
_VARIANTS: list[tuple[str, str]] = [
    (r"pro\s*max", "Pro Max"),
    (r"promax", "Pro Max"),
    (r"\bpm\b", "Pro Max"),
    (r"\bmax\b", "Pro Max"),  # no plain 'Max' line exists; in the trade it means Pro Max
    (r"\bpro\b", "Pro"),
    (r"plus", "Plus"),
    (r"\+", "Plus"),
    (r"mini", "mini"),
    (r"\bair\b", "Air"),
]

# 'xs', 'xsmax' and 'xr' are unambiguous tokens. A bare 'x' is not — it is also the
# multiplication sign in '40x iPhone 15 Pro Max', which is how most sellers write a
# quantity. So the bare form is only accepted directly after the family word, and only
# when no number follows it (which would make it 'iPhone x40', a quantity again).
_X_MODELS: list[tuple[str, str]] = [
    (r"\bxs\s*max\b", "iPhone XS Max"),
    (r"\bxsmax\b", "iPhone XS Max"),
    (r"\bxs\b", "iPhone XS"),
    (r"\bxr\b", "iPhone XR"),
    (rf"{_FAMILY}\s*x\b(?!\s*\d)", "iPhone X"),
]

_SE = re.compile(
    r"\bse\b\s*(?:\(?\s*(?P<gen>[123])(?:nd|rd|st|th)?\s*gen\w*\s*\)?|(?P<year>2016|2020|2022))?",
    re.IGNORECASE,
)
_SE_YEAR_TO_GEN = {"2016": 1, "2020": 2, "2022": 3}

# Plausible iPhone numbers. Low enough to include the 8 and 8 Plus, which still trade
# in volume; high enough to absorb several more years of releases.
_NUMBER = re.compile(r"\b(?P<n>[4-9]|[1-2]\d)\b")

# '16e' style suffix: attached to the number, not a separate word.
_E_SUFFIX = re.compile(r"\b(?P<n>\d{1,2})\s*e\b", re.IGNORECASE)


def normalise_model(raw: str | None) -> str | None:
    """Return a canonical model name, or None if the text does not name one.

    None is the honest answer for 'AirPods Pro' or 'Samsung S24'. Returning a
    best-guess iPhone would put a non-iPhone onto an iPhone board.
    """
    if not raw:
        return None

    text = _NOISE.sub(" ", raw)
    text = _STORAGE.sub(" ", text)
    text = re.sub(r"[-_/]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    if not text:
        return None

    # Split runs of digits and letters: '15pm' -> '15 pm', 'iphone15promax' ->
    # 'iphone 15 promax'. Without this, \b never falls between the number and the
    # variant, and the most common shorthand in the trade silently fails to parse.
    text = re.sub(r"(?<=\d)(?=[a-z])", " ", text)
    text = re.sub(r"(?<=[a-z])(?=\d)", " ", text)

    if _NOT_IPHONE.search(text) and not _HAS_FAMILY.search(text):
        return None

    se = _match_se(text)
    if se:
        return se

    for pattern, canonical in _X_MODELS:
        if re.search(pattern, text):
            return canonical

    number = _find_number(text)
    if number is None:
        # 'iPhone Air' carries no number.
        if re.search(rf"{_FAMILY}\s*air\b", text) or re.fullmatch(r"air", text):
            return "iPhone Air"
        return None

    variant = _find_variant(text)

    if variant:
        return f"iPhone {number} {variant}"
    if re.search(rf"\b{number}\s*e\b", text):
        return f"iPhone {number}e"
    return f"iPhone {number}"


def _match_se(text: str) -> str | None:
    if not re.search(r"\bse\b", text):
        return None

    match = _SE.search(text)
    if match:
        gen = match.group("gen")
        year = match.group("year")
        if gen:
            return f"iPhone SE ({gen}nd generation)" if gen == "2" else f"iPhone SE (gen {gen})"
        if year:
            return f"iPhone SE (gen {_SE_YEAR_TO_GEN[year]})"
    return "iPhone SE"


def _find_number(text: str) -> int | None:
    """Prefer a number that directly follows the family word; fall back to the first."""
    anchored = re.search(rf"{_FAMILY}\s*(?P<n>\d{{1,2}})", text)
    if anchored:
        candidate = int(anchored.group("n"))
        if 4 <= candidate <= 29:
            return candidate

    match = _NUMBER.search(text)
    return int(match.group("n")) if match else None


def _find_variant(text: str) -> str | None:
    for pattern, canonical in _VARIANTS:
        if re.search(pattern, text, re.IGNORECASE):
            return canonical
    return None
