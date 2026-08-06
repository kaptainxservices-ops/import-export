"""Colours: 'spc gray', 'space grey', 'SPACE GRAY' -> 'Space Gray'.

Colour is part of offer identity, so unlike the other fields this one never returns
None for an unrecognised value. It cleans and title-cases instead. Nulling an unknown
colour would collapse two genuinely different lots — a Desert Titanium and a Natural
Titanium — into one identity, and the second import would overwrite the first.

The alias table only has to cover spellings that differ, mainly grey/gray and the
abbreviations senders type by hand.
"""

import re

_ALIASES: dict[str, str] = {
    # grey / gray
    "space grey": "Space Gray",
    "space gray": "Space Gray",
    "spc grey": "Space Gray",
    "spc gray": "Space Gray",
    "sg": "Space Gray",
    "grey": "Gray",
    "gray": "Gray",
    # titanium finishes
    "nat titanium": "Natural Titanium",
    "natural ti": "Natural Titanium",
    "nat ti": "Natural Titanium",
    "blue ti": "Blue Titanium",
    "black ti": "Black Titanium",
    "white ti": "White Titanium",
    "desert ti": "Desert Titanium",
    # common shorthand
    "midnite": "Midnight",
    "star light": "Starlight",
    "prod red": "Product Red",
    "product (red)": "Product Red",
    "red": "Red",
    "rose gold": "Rose Gold",
    "sierra blue": "Sierra Blue",
    "pacific blue": "Pacific Blue",
    "deep purple": "Deep Purple",
    "alpine green": "Alpine Green",
}

_NOISE = re.compile(r"[®™()\[\],]")


def normalise_colour(raw: str | None) -> str | None:
    """Clean and canonicalise a colour. Returns None only for empty input."""
    if not raw:
        return None

    text = _NOISE.sub(" ", raw)
    text = re.sub(r"[-_/]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None

    key = text.lower()
    if key in _ALIASES:
        return _ALIASES[key]

    # Handle 'Titanium Blue' as well as 'Blue Titanium' without listing both.
    if key.endswith(" titanium") or key.startswith("titanium "):
        parts = [p for p in key.split() if p != "titanium"]
        if parts:
            return f"{' '.join(p.capitalize() for p in parts)} Titanium"

    canonical = " ".join(word.capitalize() for word in key.split())
    # British spelling normalised last, so 'Space Grey' and 'Grey' agree.
    return re.sub(r"\bGrey\b", "Gray", canonical)
