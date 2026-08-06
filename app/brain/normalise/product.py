"""One product line -> a structured spec.

This replaces the iPhone-only parser. The trade covers phones, tablets, watches,
audio, consoles, televisions and accessories, so the parser cannot assume a shape. It
extracts whatever attributes are present and leaves the rest as None.

Identity works in two tiers, and the split is what makes this tractable:

- **EAN when the supplier gives one.** Exact, global, needs no interpretation. Roughly
  half the volume in the sample has it.
- **description_key otherwise** — brand plus a normalised description with capacity,
  colour and marketing noise removed, so that 'Samsung A566 Galaxy A56 5G 128GB Gray'
  and 'A56 5G DS SM-A566B 8+128 — Awesome Graphite' can still be compared.

Two conventions in the data are worth knowing about. Samsung lists write RAM and
storage together as `8+256`, meaning 8GB RAM and 256GB storage — the first number is
never capacity. And a single line often carries several products, which is handled in
`expand_variants` rather than here.
"""

import re
from dataclasses import dataclass, field

from app.brain.normalise.brands import detect_brand, detect_category
from app.brain.normalise.colours import normalise_colour

# '8+256', '12+1TB' — RAM first, then storage.
_RAM_STORAGE = re.compile(r"\b(?P<ram>\d{1,2})\s*\+\s*(?P<storage>\d{1,4})\s*(?P<u>gb|tb)?\b",
                          re.IGNORECASE)
_CAPACITY = re.compile(r"\b(?P<n>\d{1,4})\s*(?P<u>gb|tb)\b", re.IGNORECASE)

_VALID_GB = {8, 16, 32, 64, 128, 256, 512, 1024, 2048}

# RAM sizes are a different set from storage sizes. 12GB RAM is ordinary in this
# trade; 12GB of storage does not exist. Sharing one set silently dropped the RAM
# figure from every '12+256' line.
_VALID_RAM_GB = {1, 2, 3, 4, 6, 8, 12, 16, 18, 24, 32}

_NETWORK = re.compile(r"\b(5g|4g|lte)\b", re.IGNORECASE)
_DUAL_SIM = re.compile(r"\b(?:ds|dual\s*sim|duos)\b", re.IGNORECASE)
_EDITION = re.compile(r"\b(?:ent\.?\s*ed\.?|enterprise\s*edition|ent\s*edition)\b", re.IGNORECASE)

# EANs are 8 or 13 digits. Some suppliers export them with a leading apostrophe to stop
# Excel mangling them into scientific notation.
_EAN = re.compile(r"['\s]?\b(?P<ean>\d{13}|\d{8})\b")

# Marketing adjectives Samsung attaches to colours: 'Awesome Graphite', 'Titanium Grey'.
_COLOUR_NOISE = re.compile(r"\b(?:awesome|titanium|phantom|cosmic)\b", re.IGNORECASE)

_NOISE_TOKENS = re.compile(
    r"\b(?:new|brand\s*new|sealed|original|orig|eu|non\s*eu|ds|duos|dual\s*sim|"
    r"5g|4g|lte|ent\.?\s*ed\.?|enterprise\s*edition|om|nfc|bnib)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s/+]")


@dataclass(frozen=True)
class ProductSpec:
    brand: str | None = None
    category: str | None = None
    ean: str | None = None
    capacity_gb: int | None = None
    ram_gb: int | None = None
    colour: str | None = None
    network: str | None = None
    dual_sim: bool | None = None
    edition: str | None = None
    description: str = ""
    description_key: str = ""
    warnings: list[str] = field(default_factory=list)

    def identity_key(self) -> str:
        """EAN when present, otherwise brand + normalised description + capacity + colour.

        The EAN branch is exact. The fallback is best-effort and is why colour is never
        nulled when unrecognised: dropping it would merge two different lots.
        """
        if self.ean:
            return f"ean:{self.ean}"

        parts = [
            (self.brand or "").lower(),
            self.description_key,
            str(self.capacity_gb or ""),
            (self.colour or "").lower(),
        ]
        return "spec:" + "|".join(parts)


def parse_product(text: str | None, ean: str | None = None) -> ProductSpec:
    """Parse one product line. Never raises — a bad line yields an empty spec."""
    if not text or not text.strip():
        return ProductSpec(warnings=["empty line"])

    raw = re.sub(r"\s+", " ", text).strip()

    resolved_ean = _clean_ean(ean) or _find_ean(raw)
    ram, capacity, capacity_warning = _parse_ram_and_capacity(raw)
    colour = _find_colour(raw)

    network_match = _NETWORK.search(raw)
    warnings = [capacity_warning] if capacity_warning else []

    return ProductSpec(
        brand=detect_brand(raw),
        category=detect_category(raw),
        ean=resolved_ean,
        capacity_gb=capacity,
        ram_gb=ram,
        colour=colour,
        network=network_match.group(1).upper() if network_match else None,
        dual_sim=True if _DUAL_SIM.search(raw) else None,
        edition="Enterprise Edition" if _EDITION.search(raw) else None,
        description=raw,
        description_key=build_description_key(raw),
        warnings=warnings,
    )


def build_description_key(text: str) -> str:
    """Reduce a description to something two suppliers can be compared on.

    Removes capacity, colour words, marketing adjectives and format noise, keeps model
    numbers and part codes — those are the stable part. 'Samsung A566 Galaxy A56 5G
    128GB Gray' and 'A56 5G DS SM-A566B 8+128 Awesome Graphite' both reduce to
    something centred on 'a56'/'a566'.
    """
    text = _RAM_STORAGE.sub(" ", text)
    text = _CAPACITY.sub(" ", text)
    text = _NOISE_TOKENS.sub(" ", text)
    text = _COLOUR_NOISE.sub(" ", text)
    text = _EAN.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()

    # Drop bare colour words once capacity and noise are gone.
    tokens = [t for t in text.split() if t not in _COLOUR_WORDS]
    return " ".join(tokens)


_COLOUR_WORDS = {
    "black", "white", "blue", "green", "red", "pink", "purple", "yellow", "orange",
    "gray", "grey", "silver", "gold", "graphite", "navy", "mint", "lavender",
    "charcoal", "lilac", "olive", "teal", "sage", "starlight", "midnight", "cream",
    "beige", "bronze", "copper", "violet", "cobalt", "sky", "jet", "shadow", "icy",
    "lightblue", "light",
}


def _parse_ram_and_capacity(raw: str) -> tuple[int | None, int | None, str | None]:
    """Samsung's '8+256' means 8GB RAM and 256GB storage — the first number is RAM."""
    match = _RAM_STORAGE.search(raw)
    if match:
        ram = int(match.group("ram"))
        storage = int(match.group("storage"))
        if (match.group("u") or "").lower() == "tb":
            storage *= 1024
        return (
            ram if ram in _VALID_RAM_GB else None,
            storage if storage in _VALID_GB else None,
            None if storage in _VALID_GB else f"implausible capacity {storage}",
        )

    match = _CAPACITY.search(raw)
    if match:
        value = int(match.group("n"))
        if (match.group("u") or "").lower() == "tb":
            value *= 1024
        if value in _VALID_GB:
            return None, value, None
        return None, None, f"implausible capacity {value}"

    return None, None, None


def _find_colour(raw: str) -> str | None:
    """Pick the colour words out of a description.

    Multi-colour lines like 'Black / Blue / Gray' are one product offered in three
    finishes, and expanding them is `expand_variants`' job, not this one. Here the
    first is taken so the line still carries a colour.
    """
    candidates: list[str] = []
    for token in re.split(r"[/,·]| - |—", raw):
        words = [w for w in _PUNCT.sub(" ", token).split() if w.lower() in _COLOUR_WORDS]
        if words:
            candidates.append(" ".join(words))

    if not candidates:
        return None
    return normalise_colour(candidates[0])


def _find_ean(raw: str) -> str | None:
    match = _EAN.search(raw)
    return match.group("ean") if match else None


def _clean_ean(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) in (8, 13) else None
