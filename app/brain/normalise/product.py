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
from app.brain.normalise.grades import normalise_grade
from app.brain.normalise.regions import normalise_region_code

# '8+256', '12+1TB', '8/256' — RAM first, then storage. The slash form is Xiaomi's.
_RAM_STORAGE = re.compile(
    r"\b(?P<ram>\d{1,2})\s*[+/]\s*(?P<storage>\d{1,4})\s*(?P<u>gb|tb)?\b", re.IGNORECASE
)
_CAPACITY = re.compile(r"\b(?P<n>\d{1,4})\s*(?P<u>gb|tb)\b", re.IGNORECASE)

# Two sizes in a row, both carrying a unit and neither saying which is which:
# 'MacBook Neo 13" A18 Pro 6C CPU 5C GPU 8GB 256GB'. Read left to right the first wins,
# and 8 is a legal storage size, so the row took 8GB as its capacity — which made every
# storage variant of that machine one identity.
_RAM_THEN_STORAGE = re.compile(
    r"\b(?P<ram>\d{1,2})\s*gb\s+(?P<storage>\d{2,4})\s*(?P<u>gb|tb)\b", re.IGNORECASE
)

# '12GB RAM' names the memory, not the storage. Without this the first size on
# 'Dual Sim 6GB RAM 128GB Black' wins, is rejected as an impossible storage size, and
# the row reaches the board with no capacity at all.
_NAMES_RAM = re.compile(r"\s*(?:ram|memory)\b", re.IGNORECASE)

# What a supplier writes when the figure came out of a spreadsheet column rather than a
# marketing name. Nobody ships a 1000GB phone; they mean 1TB.
_ROUNDED = {1000: 1024, 2000: 2048}

# 'APPLE IPHONE AIR 1TGB SKY BLUE' — a typo for 1TB, made consistently by one supplier.
_TYPO_TB = re.compile(r"\b(?P<n>\d{1,2})\s*t\s*gb\b", re.IGNORECASE)

# A capacity written with no unit at all: 'IPHONE 17 PRO MAX 256 COSMIC ORANGE'.
# Only 128 and up are read bare, because 16, 32 and 64 are also model numbers and
# 'Apple iPhone 16 Black' must not become a 16GB handset. The lookahead keeps a
# quantity out of it — '128 pcs' is a count, not a size.
_BARE_CAPACITY = re.compile(
    r"\b(?P<n>128|256|512|1024|2048)\b(?!\s*(?:pcs|pieces|units?|stk|pz|ks)\b)",
    re.IGNORECASE,
)

_VALID_GB = {8, 16, 32, 64, 128, 256, 512, 1024, 2048}

# RAM sizes are a different set from storage sizes. 12GB RAM is ordinary in this
# trade; 12GB of storage does not exist. Sharing one set silently dropped the RAM
# figure from every '12+256' line.
_VALID_RAM_GB = {1, 2, 3, 4, 6, 8, 12, 16, 18, 24, 32}

_NETWORK = re.compile(r"\b(5g|4g|lte)\b", re.IGNORECASE)
_DUAL_SIM = re.compile(r"\b(?:ds|dual\s*sim|duos)\b", re.IGNORECASE)
_EDITION = re.compile(r"\b(?:ent\.?\s*ed\.?|enterprise\s*edition|ent\s*edition)\b", re.IGNORECASE)

# 'OM' is open-market stock: the same handset without a carrier's or a region's
# customisation, and priced differently. Suppliers list it beside the standard lot.
_OPEN_MARKET = re.compile(r"\bom\b", re.IGNORECASE)

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
    # Grade and region are read but deliberately kept OUT of identity_key below. They
    # describe a lot rather than name a product, and the database's generated column
    # mirrors that exactly — the two expressions have to agree or reconciliation starts
    # creating duplicates instead of updating rows.
    grade: str | None = None
    region_code: str | None = None
    network: str | None = None
    dual_sim: bool | None = None
    edition: str | None = None
    description: str = ""
    description_key: str = ""
    match_key: str = ""
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


def parse_product(
    text: str | None,
    ean: str | None = None,
    colour: str | None = None,
) -> ProductSpec:
    """Parse one product line. Never raises — a bad line yields an empty spec.

    `colour` is the value from a dedicated colour column, and it takes precedence over
    anything read out of the description. Sheets that separate colour out do not repeat
    it in the description, so without this the three rows 'Echo Spot 2024 speaker' in
    black, blue and white all reduce to one identity — and two of the three vanish into
    the third. Masterfone's list alone loses 27% of its rows that way.
    """
    if not text or not text.strip():
        return ProductSpec(warnings=["empty line"])

    raw = re.sub(r"\s+", " ", text).strip()

    resolved_ean = _clean_ean(ean) or _find_ean(raw)
    ram, capacity, capacity_warning = _parse_ram_and_capacity(raw)
    resolved_colour = normalise_colour(colour) if colour else _find_colour(raw)

    network_match = _NETWORK.search(raw)
    warnings = [capacity_warning] if capacity_warning else []

    return ProductSpec(
        brand=detect_brand(raw),
        category=detect_category(raw),
        ean=resolved_ean,
        capacity_gb=capacity,
        ram_gb=ram,
        colour=resolved_colour,
        grade=normalise_grade(raw),
        region_code=normalise_region_code(raw),
        network=network_match.group(1).upper() if network_match else None,
        dual_sim=True if _DUAL_SIM.search(raw) else None,
        edition="Enterprise Edition" if _EDITION.search(raw) else None,
        description=raw,
        description_key=build_description_key(raw),
        match_key=build_match_key(raw),
        warnings=warnings,
    )


def build_description_key(text: str) -> str:
    """Reduce a description to something two suppliers can be compared on.

    Removes capacity, colour words, marketing adjectives and format noise, keeps model
    numbers and part codes — those are the stable part. 'Samsung A566 Galaxy A56 5G
    128GB Gray' and 'A56 5G DS SM-A566B 8+128 Awesome Graphite' both reduce to
    something centred on 'a56'/'a566'.
    """
    # Canonicalised before anything is discarded, and deliberately NOT discarded.
    #
    # An Enterprise Edition lot and a standard one are different stock at different
    # prices, and Automic lists both on consecutive lines. Treating 'Ent. Ed.' as noise
    # made the two one identity, so the second line overwrote the first and one of the
    # prices never reached the board. Same for open-market stock.
    #
    # One canonical spelling rather than the supplier's, so a list saying 'Ent. Ed.' on
    # Monday and 'Enterprise Edition' on Tuesday still updates one row instead of
    # opening a second alongside it.
    text = _EDITION.sub(" entedition ", text)
    text = _OPEN_MARKET.sub(" openmarket ", text)

    # The memory figure stays too. '12+256' and '8+256' are different phones and are
    # priced apart; dropping the pair wholesale left them sharing one identity. Storage
    # is not kept here — it is already its own column in the key.
    text = _RAM_STORAGE.sub(lambda m: f" ram{m.group('ram')} ", text)
    text = _CAPACITY.sub(" ", text)
    text = _NOISE_TOKENS.sub(" ", text)
    text = _COLOUR_NOISE.sub(" ", text)
    text = _EAN.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()

    # Drop bare colour words once capacity and noise are gone.
    tokens = [t for t in text.split() if t not in _COLOUR_WORDS]
    return " ".join(tokens)


# Words that identify *which product* something is, as opposed to describing it.
# Everything outside this set is dropped from the match key.
_MODEL_WORDS = {
    # product lines
    "iphone", "ipad", "ipod", "macbook", "imac", "airpods", "airpod", "airtag", "watch",
    "pixel", "galaxy", "tab", "note", "redmi", "poco", "moto", "edge", "razr", "xcover",
    "switch", "playstation", "xbox", "console", "buds", "band", "nord",
    # variants
    "pro", "max", "plus", "mini", "air", "ultra", "fold", "flip", "se", "xr", "xs",
    "fe", "lite", "series", "wifi", "cellular", "gen", "e",
    # brands, so 'Apple iPhone' and 'iPhone' do not diverge
    "apple", "samsung", "google", "xiaomi", "motorola", "sony", "nintendo", "honor",
    "oppo", "oneplus", "vivo", "realme", "nokia", "huawei", "nothing", "amazfit",
}

_SIZE_TOKEN = re.compile(r"^\d+(?:mm|w|inch|in|\")?$", re.IGNORECASE)
_MODEL_CODE = re.compile(r"^(?:sm[-_]?)?[a-z]{1,3}\d{2,4}[a-z]?$", re.IGNORECASE)


def build_match_key(text: str) -> str:
    """A looser key, used only for matching buyers to sellers — never for identity.

    `description_key` keeps everything it cannot confidently discard, which is right for
    identity: merging two products that are not the same destroys stock. But it leaves
    marketing colour words behind, and a seller line reducing to
    'apple iphone 16 bluegreen ultramarin' will never meet a buyer's 'apple iphone 16'.

    So this keeps only tokens that say *which product* something is — product line,
    variant, model number, size — and drops every adjective. The asymmetry is
    deliberate: a wrong identity silently overwrites real stock, while a wrong match
    shows a suggestion that a human dismisses in a second.
    """
    text = _RAM_STORAGE.sub(" ", text)
    text = _CAPACITY.sub(" ", text)
    text = _EAN.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()

    kept = [
        token
        for token in text.split()
        if token in _MODEL_WORDS or _SIZE_TOKEN.match(token) or _MODEL_CODE.match(token)
    ]

    # Repeated tokens carry no extra information and vary between suppliers.
    seen: set[str] = set()
    unique = [t for t in kept if not (t in seen or seen.add(t))]

    # The manufacturer's name is dropped, because whether a supplier writes it is a
    # habit rather than a fact about the product. A buyer's 'IPHONE 15 128GB black'
    # reduced to 'iphone 15' and a seller's 'Apple iPhone 15 128GB Pink' to
    # 'apple iphone 15' — the same handset, and they could never meet.
    #
    # Only when something is left. 'Apple' on its own is all the key there is, and an
    # empty key matches nothing. And only the maker's name: 'Redmi' and 'Galaxy' are
    # product lines that carry real meaning, so they stay.
    without_brand = [t for t in unique if t not in _MAKER_WORDS]
    if without_brand:
        unique = without_brand

    # A key must be specific enough to mean something. '4smarts Pico Dual 20W Car
    # Charger' reduces to '20w', which would match every other 20W accessory on the
    # board. An empty key means "no reliable match key"; the caller falls back to the
    # stricter description key, and accessories match on EAN as they should.
    named = any(t in _MODEL_WORDS for t in unique)
    coded = sum(1 for t in unique if _MODEL_CODE.match(t))
    if not named and coded < 2:
        return ""

    return " ".join(unique)


# Manufacturer names only. Deliberately excludes product lines — 'redmi', 'galaxy',
# 'poco', 'moto' — which distinguish one product from another and must survive.
_MAKER_WORDS = {
    "apple", "samsung", "xiaomi", "google", "motorola", "nokia", "sony", "huawei",
    "honor", "oppo", "vivo", "realme", "oneplus", "asus", "lenovo", "tcl", "zte",
    "alcatel", "hmd", "infinix", "tecno", "ulefone", "doogee", "blackview",
}

_COLOUR_WORDS = {
    "black", "white", "blue", "green", "red", "pink", "purple", "yellow", "orange",
    "gray", "grey", "silver", "gold", "graphite", "navy", "mint", "lavender",
    "charcoal", "lilac", "olive", "teal", "sage", "starlight", "midnight", "cream",
    "beige", "bronze", "copper", "violet", "cobalt", "sky", "jet", "shadow", "icy",
    "lightblue", "light",
    # Apple's titanium finishes, which are how every iPhone 15 Pro and 16 Pro is
    # described — 'Natural Ti', 'Blue Ti', 'Desert Titanium'. Without these the colour
    # comes back empty for the entire Pro range, which is the top of the market.
    # Identity still separated the lots, because the words survive in description_key;
    # what was lost was the colour a trader reads off the row.
    "titanium", "ti", "natural", "desert", "ultramarine",
}


def _parse_ram_and_capacity(raw: str) -> tuple[int | None, int | None, str | None]:
    """Samsung's '8+256' means 8GB RAM and 256GB storage — the first number is RAM.

    Capacity is part of identity, so a handset that reaches the board without one does
    not merely look incomplete: it collides with every other capacity of the same model
    in the same colour, and one of the two prices silently never appears. Each branch
    below is a shape a real supplier writes that used to produce nothing at all.
    """
    # A RAM+storage pair, believed only when BOTH halves are plausible. 'iPhone 15/15
    # Pro 256GB' is a compatibility list, not 15GB of RAM — and reading it as one used
    # to stop the search before the real capacity further along the line.
    for match in _RAM_STORAGE.finditer(raw):
        ram = int(match.group("ram"))
        storage = int(match.group("storage"))
        if (match.group("u") or "").lower() == "tb":
            storage *= 1024
        storage = _ROUNDED.get(storage, storage)
        if ram in _VALID_RAM_GB and storage in _VALID_GB:
            return ram, storage, None

    # Two sizes side by side, smaller first: memory then storage, the same reading as
    # '8+256'. Believed only when each half is plausible for its own role.
    match = _RAM_THEN_STORAGE.search(raw)
    if match:
        ram = int(match.group("ram"))
        storage = int(match.group("storage"))
        if (match.group("u") or "").lower() == "tb":
            storage *= 1024
        storage = _ROUNDED.get(storage, storage)
        if ram in _VALID_RAM_GB and storage in _VALID_GB and ram < storage:
            return ram, storage, None

    # A size carrying a unit. Any the line goes on to call RAM is kept as the RAM
    # figure and passed over, rather than being taken for the storage.
    ram_named: int | None = None
    implausible: int | None = None
    for match in _CAPACITY.finditer(raw):
        value = int(match.group("n"))
        if (match.group("u") or "").lower() == "tb":
            value *= 1024
        if _NAMES_RAM.match(raw, match.end()):
            if ram_named is None and value in _VALID_RAM_GB:
                ram_named = value
            continue
        value = _ROUNDED.get(value, value)
        if value in _VALID_GB:
            return ram_named, value, None
        if implausible is None:
            implausible = value

    match = _TYPO_TB.search(raw)
    if match:
        value = int(match.group("n")) * 1024
        if value in _VALID_GB:
            return ram_named, value, None

    match = _BARE_CAPACITY.search(raw)
    if match:
        return ram_named, int(match.group("n")), None

    if implausible is not None:
        return ram_named, None, f"implausible capacity {implausible}"

    return ram_named, None, None


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
            # 'Starlight Starlight' happens when a supplier repeats the colour in the
            # description and again in a bracketed note. Two words is also the ceiling —
            # 'Space Gray' is a colour, but 'Gold Beige Rubber' is a colour plus a strap.
            seen: set[str] = set()
            deduped = [w for w in words if not (w.lower() in seen or seen.add(w.lower()))]
            candidates.append(" ".join(deduped[:2]))

    if not candidates:
        return None
    return normalise_colour(candidates[0])


def _find_ean(raw: str) -> str | None:
    match = _EAN.search(raw)
    return match.group("ean") if match else None


def normalise_gtin(value: str | None) -> str | None:
    """Reduce any barcode form to a canonical GTIN, or None if it is not one.

    Everything that can be is expressed as GTIN-13, because suppliers are inconsistent
    about which form they send and identity has to survive that. A US supplier's 12-digit
    UPC and a European supplier's 13-digit EAN describe the same physical product; left
    unpadded they would key as two different offers and never match, which defeats the
    whole point of using the barcode as identity.

        12 digits (UPC-A)          -> left-padded to 13
        14 digits with a leading 0 -> the trailing 13
        8 digits (GTIN-8)          -> kept as-is
    """
    if not value:
        return None

    digits = re.sub(r"\D", "", str(value))

    if len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 12:
        digits = "0" + digits

    return digits if len(digits) in (8, 13) else None


def _clean_ean(value: str | None) -> str | None:
    return normalise_gtin(value)
