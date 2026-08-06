"""Storage: '256GB', '256 gb', '1TB', '1 tb' -> gigabytes as an integer."""

import re

_WITH_UNIT = re.compile(r"(?P<n>\d{1,4})\s*(?P<unit>gb|tb)\b", re.IGNORECASE)
_BARE = re.compile(r"\b(?P<n>\d{2,4})\b")

# A bare number only means storage if it is a size Apple actually ships. Without this
# guard, the 40 in '40 pcs' becomes a 40GB phone.
_VALID_GB = {16, 32, 64, 128, 256, 512, 1024, 2048}


def normalise_storage_gb(raw: str | None) -> int | None:
    if not raw:
        return None

    match = _WITH_UNIT.search(raw)
    if match:
        value = int(match.group("n"))
        if match.group("unit").lower() == "tb":
            value *= 1024
        return value if value in _VALID_GB else None

    for candidate in _BARE.findall(raw):
        value = int(candidate)
        if value in _VALID_GB:
            return value

    return None
