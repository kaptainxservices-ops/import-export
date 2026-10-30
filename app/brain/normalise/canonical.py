"""Which model a line names, written one way however the supplier wrote it.

The same handset reaches the board as 'SAM S23', 'Samsung Galaxy S23', 'Galaxy S23 5G
DS 8/128GB' and 'S23 128GB Phantom Black'. A buyer's 'Samsung S23' has to meet every
one of them, and none of them may meet an S23 Ultra, an S23 FE or an S23 case. This
module produces the one string they all share:

    'SAM S23 128G Black'                       -> samsung:s23
    'Samsung Galaxy S23 5G 8/128GB'            -> samsung:s23
    'Galaxy S24+ 256GB'  /  'SAM S24 PLUS'     -> samsung:s24plus
    'Galaxy Z Flip7 FE'  /  'SAM FLIP 7FE'     -> samsung:flip7fe
    'S25FE 5G 128GB 8GB (SM-S731/DS) NEW'      -> samsung:s25fe
    'IP15PM 256'  /  'Apple iPhone 15 Pro Max' -> apple:iphone15promax
    'HONOR MAGICPAD4 WiFi 512GB'               -> honor:magicpad4
    'HONOR MAGIC PAD 4 WIFI 256GB'             -> honor:magicpad4

How: the brand is settled first (from the text, a brand column, or the model's own
shape), then everything that describes a lot rather than naming a model is removed --
storage, memory, colour, network, condition, barcodes, part codes in brackets,
marketing words -- and what is left is joined without spaces, so 'Magic Pad 4' and
'MagicPad4' cannot differ by a space.

Variant words are never removed. Pro, Max, Plus, Ultra, FE, Lite, Edge, Mini, Fold and
Flip are what separate one model from the next, so two lines differing by one of them
can never share a key. That is the property matching depends on, and the tests hold it
to a long list of real pairs in both directions.

Network generation and Wi-Fi/cellular are read out separately rather than kept in the
key. A buyer writing 'S23' means the S23 whether or not the supplier wrote '5G', but a
Galaxy A16 4G and an A16 5G are different phones -- so matching compares the two
attributes when both sides state them, and ignores them when only one does.

Deterministic, no network, never raises. Lines the rules cannot settle confidently are
flagged with `confident=False`; app/brain/resolve_models.py asks a model about those,
once per wording, and remembers the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.brain.normalise.models import normalise_model


@dataclass(frozen=True)
class CanonicalModel:
    brand: str | None = None
    # 'samsung:s23ultra'. Empty when the line names nothing recognisable.
    key: str = ""
    # The part after the brand, for comparing against a line whose brand is unknown.
    model: str = ""
    # The words the model was joined from, for telling a near miss from a different
    # product: 'nord ce6 smooth' against 'nord ce6' differs by one descriptive word.
    tokens: tuple[str, ...] = ()
    category: str | None = None
    # '4g' or '5g' for handsets; 'wifi' or 'cellular' for tablets and watches.
    network: str | None = None
    connectivity: str | None = None
    # False when the rules had to guess: no brand anywhere, or nothing in the model that
    # looks like a model (no number, no known product line).
    confident: bool = False


# ---------------------------------------------------------------- brands

# Every spelling seen in a brand column or a description, including the typos real
# sheets carry ('XIOAMI'). Keys are lowercased, punctuation-free.
_BRAND_NAMES: dict[str, str] = {
    "samsung": "Samsung", "sam": "Samsung", "sams": "Samsung", "samung": "Samsung",
    "ssg": "Samsung", "galaxy": "Samsung",
    "apple": "Apple",
    "google": "Google",
    "xiaomi": "Xiaomi", "xioami": "Xiaomi", "xiomi": "Xiaomi", "xaomi": "Xiaomi",
    "redmi": "Xiaomi", "poco": "Xiaomi",
    "motorola": "Motorola", "moto": "Motorola", "mot": "Motorola",
    "oneplus": "OnePlus", "one plus": "OnePlus", "1+": "OnePlus",
    "honor": "Honor", "huawei": "Huawei",
    "oppo": "Oppo", "vivo": "Vivo", "realme": "Realme", "nokia": "Nokia", "hmd": "HMD",
    "nothing": "Nothing", "sony": "Sony", "nintendo": "Nintendo", "microsoft": "Microsoft",
    "jbl": "JBL", "hp": "HP", "logitech": "Logitech", "marshall": "Marshall",
    "soundcore": "Anker", "anker": "Anker", "sennheiser": "Sennheiser", "sonos": "Sonos",
    "ray ban": "Ray-Ban", "rayban": "Ray-Ban", "meta": "Ray-Ban",
    "jlab": "JLab", "jio": "Jio", "zebronics": "Zebronics", "philips": "Philips",
    "pebble": "Pebble", "hammer": "Hammer", "artix": "Artix", "amazfit": "Amazfit",
    "garmin": "Garmin", "lenovo": "Lenovo", "asus": "Asus", "tcl": "TCL", "zte": "ZTE",
    "dji": "DJI", "gopro": "GoPro", "dyson": "Dyson", "beats": "Apple", "belkin": "Belkin",
    "lg": "LG", "acer": "Acer", "dell": "Dell", "tecno": "Tecno", "infinix": "Infinix",
}

# Words naming the maker, removed from the model once the brand is known. A supplier
# writing the brand or not is a habit, not a fact about the product.
_MAKER_TOKENS = {
    "samsung", "sam", "sams", "apple", "google", "xiaomi", "xioami", "xiomi", "motorola",
    "mot", "oneplus", "honor", "huawei", "oppo", "vivo", "realme", "nokia", "sony",
    "jbl", "hp", "logitech", "marshall", "soundcore", "anker", "sennheiser", "sonos",
    "jlab", "jio", "zebronics", "philips", "pebble", "hammer", "artix", "nothing",
    "amazfit", "garmin", "lenovo", "asus", "tcl", "zte", "lg", "rayban", "hmd", "beats",
}

# Product-line words that some suppliers write and others leave out, per brand. Removing
# them is what lets 'Galaxy S23' meet 'S23' and 'Moto G06' meet 'Motorola G06'. Only
# words that never distinguish two models of that brand belong here.
_OPTIONAL_LINE_WORDS: dict[str, set[str]] = {
    "Samsung": {"galaxy", "gal"},
    "Motorola": {"moto"},
    "Xiaomi": {"mi"},
    "Google": set(),
    "OnePlus": set(),
    "Apple": set(),
}


def known_brand(text: str | None) -> str | None:
    """A brand column cell naming a maker we know, else None.

    Preferred over reading the description: 'MAKE: SAMSUNG' beside 'GALAXY WATCH
    ULTRA' is the supplier's own answer, where the description is a string to be guessed
    at. Unknown makers fall through to the description first.
    """
    if not text or not str(text).strip():
        return None
    cleaned = re.sub(r"[^a-z0-9+ ]", " ", str(text).lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in _BRAND_NAMES:
        return _BRAND_NAMES[cleaned]
    words = cleaned.split()
    if words and words[0] in _BRAND_NAMES and len(words) <= 2:
        return _BRAND_NAMES[words[0]]
    return None


def canonical_brand(text: str | None) -> str | None:
    """A brand column cell, or a section heading, as one canonical name."""
    if not text or not str(text).strip():
        return None
    cleaned = re.sub(r"[^a-z0-9+ ]", " ", str(text).lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    if cleaned in _BRAND_NAMES:
        return _BRAND_NAMES[cleaned]
    first = cleaned.split()[0]
    if first in _BRAND_NAMES and len(cleaned.split()) <= 2:
        return _BRAND_NAMES[first]
    # A maker the description reader knows by another spelling: 'SANDISK' in a brand
    # column is the 'SanDisk' that detect_brand returns, not a second brand 'Sandisk'.
    display = _display_names().get(cleaned)
    if display:
        return display
    # An unknown maker is still a maker. Kept as written, tidied, so two sheets that both
    # say 'ARTIX' agree -- but never a long string: a sentence is not a brand.
    if len(cleaned) <= 20 and len(cleaned.split()) <= 2 and not any(c.isdigit() for c in cleaned):
        return cleaned.title()
    return None


def _display_names() -> dict[str, str]:
    from app.brain.normalise.brands import _BRAND_ALIASES

    names = {name.lower(): name for _, name in _BRAND_ALIASES if name != "Other"}
    names.update({"wd": "WD", "western digital": "WD", "tp link": "TP-Link"})
    return names


# ---------------------------------------------------------------- vocabulary

_COLOUR_WORDS = {
    "black", "white", "blue", "green", "red", "pink", "purple", "yellow", "orange",
    "gray", "grey", "silver", "gold", "graphite", "navy", "mint", "lavender", "charcoal",
    "lilac", "olive", "teal", "sage", "starlight", "midnight", "cream", "beige", "bronze",
    "copper", "violet", "cobalt", "sky", "jet", "jetblack", "shadow", "icy", "lightblue",
    "light", "dark", "deep", "titanium", "ti", "natural", "desert", "ultramarine",
    "phantom", "awesome", "cosmic", "pitch", "glacier", "glacial", "golden", "pantone",
    "tapestry", "nautical", "tea", "titan", "matte", "shiny", "glossy", "rose", "ice",
    "ocean", "forest", "coral", "pearl", "frost", "frosted", "arctic", "onyx", "obsidian",
    "porcelain", "hazel", "peony", "aqua", "emerald", "sapphire", "ruby", "amber",
    "champagne", "sand", "marble", "misty", "lime", "rosegold", "space", "gunmetal",
    "gun", "metal", "burgundy", "burgandy", "clear", "transparent", "polar", "gradient",
    "jade", "moonlight", "sunset", "ivory", "smoke", "storm", "steel", "platinum",
    "iceblue", "sea", "nordic", "cloud", "mist", "stone", "slate", "plum", "berry",
    "crimson", "scarlet", "indigo", "denim", "khaki", "chocolate", "coffee", "mocha",
    "caramel", "peach", "salmon", "fuchsia", "magenta", "turquoise", "cyan", "lemon",
    "sunrise", "dusk", "dawn", "aurora", "galactic", "lunar", "solar", "orchid", "lilly",
    "blush", "snow", "carbon", "pure", "jetblk", "blk", "wht", "gry", "blu",
}

# Words that describe the condition, packaging or paperwork of a lot.
_NOISE = {
    "new", "brand", "sealed", "original", "orig", "bnib", "boxed", "box", "retail",
    "ds", "dual", "sim", "duos", "dualsim", "single", "esim", "nfc", "unlocked",
    "eu", "uk", "non", "india", "indian", "spec", "global", "version", "intl",
    "international", "europe", "european", "us", "usa", "uae", "hk", "warranty",
    "stock", "ready", "smartphone", "mobile", "handset", "cellphone", "the", "with",
    "and", "of", "pcs", "pc", "units", "unit", "only", "ex", "exw", "fob", "pack",
    "openbox", "refurbished", "refurb", "used", "grade", "graded", "cpo", "mixed",
    "colour", "colours", "color", "colors", "assorted", "any", "random", "all", "mix",
    "welcome", "welcomed", "options", "versions",
    "ram", "rom", "memory", "storage",
    # Edition is read into a column of its own (see product._edition) and compared
    # there; in the key it would split 'S24 Ultra' from 'S24 Ultra Ent. Ed.' twice.
    "ent", "ed", "enterprise", "edition", "om", "openmarket",
    # Marketing that some suppliers print and others do not: 'Galaxy S25 Ultra AI'.
    "ai",
    # What kind of thing it is, restated. 'JBL Wave Beam 2 Earbuds' and 'JBL Wave Beam
    # 2' are one product; category already separates earbuds from a speaker. Words
    # that name a line ('buds', 'watch', 'pad') are not here and never will be.
    "earbuds", "earbud", "earphones", "earphone", "headphones", "headphone", "headset",
    "speaker", "speakers", "wireless", "bluetooth", "portable", "protable", "tws",
    "true", "over", "ear", "overear", "inear", "smart", "glasses", "sunglasses",
}

# Never removed, and never treated as mere description. Each one separates a model
# from its neighbour -- or, for the last line, an accessory from the thing it fits.
VARIANT_WORDS = frozenset({
    "pro", "max", "plus", "ultra", "fe", "lite", "edge", "mini", "fold", "flip", "neo",
    "air", "e", "s", "x", "t", "r", "u", "se", "prime", "power", "play", "fusion",
    "classic", "active", "slim", "turbo", "note", "go", "core", "ce", "xl", "anc", "kids",
    "case", "cover", "keyboard", "pencil", "charger", "cable", "strap", "glass",
    "protector", "adapter", "folio", "stand", "pen", "mouse", "dock", "band",
})
_VARIANT_WORDS = VARIANT_WORDS


def is_descriptive(word: str) -> bool:
    """A word that may describe a model without naming a different one: alphabetic,
    not a variant. 'smooth', 'deluxe', 'retro' -- never 'pro' or 'x6b'."""
    return word.isalpha() and len(word) >= 2 and word not in VARIANT_WORDS


def differs_only_in_description(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Two token lists that share their model number and differ only in words that
    describe. The only pairs the model resolver may ever be asked about -- an S23 and
    an S23 Ultra never qualify, whatever any model says about them."""
    left, right = set(a), set(b)
    if not left or not right or left == right:
        return False
    shared = left & right
    if not any(any(c.isdigit() for c in word) for word in shared):
        return False
    return all(is_descriptive(word) for word in left ^ right)

_NETWORK = re.compile(r"(?<![a-z0-9])(5g|4g|3g|lte)(?![a-z0-9])")
_WIFI = re.compile(r"(?<![a-z0-9])(?:wi\s*fi|wlan|wifi)(?![a-z0-9])")
_CELLULAR = re.compile(r"(?<![a-z0-9])(?:cellular|lte|5g|4g|sim)(?![a-z0-9])")

_EAN = re.compile(r"(?<!\d)\d{8}(?:\d{4,6})?(?!\d)")
_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_RAM_PAIR = re.compile(r"(?<![a-z0-9])\d{1,2}\s*[+/]\s*\d{2,4}\s*(?:gb|tb|g|t)?(?![a-z0-9])")
_SIZES = re.compile(
    r"(?<![a-z0-9])\d{1,4}\s*(?:gb|tb|go|to)(?![a-z0-9])"
    r"|(?<![a-z0-9])(?:16|32|64|128|256|512|1024)\s*g(?![a-z0-9])"
    r"|(?<![a-z0-9])[12]\s*t(?![a-z0-9])"
)
_IPHONE = re.compile(r"(?<![a-z])(?:iphone|ip\s*\d{1,2})(?![a-z])")
_APPLE_NUMBER = re.compile(r"(?<![a-z])\d{1,2}\s*(?:pro|max|plus|pm|e|mini)?(?![a-z0-9])")
_APPLE_OTHER = re.compile(r"(?<![a-z])(?:ipad|macbook|imac|watch|airpods?|airtag|mac)(?![a-z])")
_GRADE_PHRASE = re.compile(r"(?<![a-z0-9])grade\s*[a-e]{1,3}[+\-]?(?![a-z0-9])")


def canonical_model(
    text: str | None, brand: str | None = None, colour: str | None = None
) -> CanonicalModel:
    """The canonical model of one product line. Never raises."""
    try:
        return _canonical(text, brand, colour)
    except Exception:  # noqa: BLE001 -- a bad line yields no key, never an exception
        return CanonicalModel()


def _canonical(text: str | None, brand: str | None, colour: str | None) -> CanonicalModel:
    if not text or not text.strip():
        return CanonicalModel()

    lowered = re.sub(r"\s+", " ", str(text).lower()).strip()

    # Network and Wi-Fi are attributes, read before anything is removed.
    network_match = _NETWORK.search(lowered)
    network = None
    if network_match:
        found = network_match.group(1)
        network = "4g" if found in ("4g", "lte") else found

    # Bracketed groups are part codes and notes -- '(SM-S731/DS)', '(A3714)',
    # '(XT2535-3)'. Buyers never write them, so they cannot be part of the key.
    body = _BRACKETS.sub(" ", lowered)
    body = _EAN.sub(" ", body)
    body = _GRADE_PHRASE.sub(" ", body)
    body = _RAM_PAIR.sub(" ", body)

    # Wi-Fi or cellular is an attribute that is often written after the storage size
    # ('iPad Air 11 M3 128GB Wi-Fi'), so it is read before the tail is dropped below.
    connectivity = None
    if _WIFI.search(body):
        connectivity = "wifi"
    elif _CELLULAR.search(body):
        connectivity = "cellular"

    # Everything after the storage size is finish, market and notes, never the model:
    # 'Pixel 10 Pro 128GB Porcelain', 'Galaxy S25 Ultra 512GB Titanium Silverblue',
    # 'iPhone 16 128GB Black ZD/A'. Every season brings colour names no vocabulary has
    # yet, and each one left in the key made the same phone a different model -- a near
    # miss instead of a fill. Kept only when the size comes first and nothing names the
    # model before it ('128GB iPhone 16').
    size = _SIZES.search(body)
    if size and re.search(r"[a-z]{2,}", body[: size.start()]):
        body = body[: size.start()]
    body = _SIZES.sub(" ", body)
    # '12gb ram' leftovers, '8gb' after the storage was taken.
    body = re.sub(r"(?<![a-z0-9])\d{1,2}\s*gb(?![a-z0-9])", " ", body)

    body = _NETWORK.sub(" ", body)
    body = _WIFI.sub(" ", body)
    body = re.sub(r"(?<![a-z0-9])cellular(?![a-z0-9])", " ", body)

    # '+' as a variant: 'S24+' is the S24 Plus. A '+' between numbers went with the RAM
    # pair above.
    body = re.sub(r"(?<=[a-z0-9])\s*\+", " plus ", body)
    body = re.sub(r"[^a-z0-9 ]", " ", body)
    body = re.sub(r"(?<![a-z0-9])one\s+plus(?![a-z0-9])", " oneplus ", body)
    body = re.sub(r"(?<![a-z0-9])ray\s+ban(?![a-z0-9])", " rayban ", body)
    body = re.sub(r"(?<![a-z0-9])i\s+phone(?![a-z0-9])", " iphone ", body)
    body = re.sub(r"(?<![a-z0-9])promax(?![a-z0-9])", " pro max ", body)

    # Split glued variants off their numbers: 's25fe' -> 's25 fe', '15pm' -> '15 pm',
    # 'flip7' -> 'flip 7'. Only a word of three letters or more is split from a number
    # that follows it, so model codes like 's23', 'a56' and 'x6b' stay whole.
    body = re.sub(r"(?<=\d)(?=[a-z])", " ", body)
    body = re.sub(r"(?<![a-z])([a-z]{3,})(?=\d)", r"\1 ", body)
    tokens = body.split()

    # A quantity written onto the line: 'S24 FE x50', '50x'. Only at the end, and only
    # after a model number, so that 'Vivo X200' keeps its name.
    if len(tokens) >= 2 and re.fullmatch(r"x\d{1,4}", tokens[-1]) and any(
        any(c.isdigit() for c in t) for t in tokens[:-1]
    ):
        tokens = tokens[:-1]
    tokens = [t for t in tokens if not re.fullmatch(r"\d{1,4}x", t)]

    resolved_brand = canonical_brand(brand) if brand else None
    if resolved_brand is None:
        for token in tokens:
            if token in _BRAND_NAMES and token not in {"meta", "moto", "redmi", "poco", "nothing"}:
                resolved_brand = _BRAND_NAMES[token]
                break
        else:
            if "rayban" in tokens:
                resolved_brand = "Ray-Ban"
            elif "nothing" in tokens and any(t in ("phone", "ear", "watch") for t in tokens):
                resolved_brand = "Nothing"
            elif any(t in ("redmi", "poco") for t in tokens):
                resolved_brand = "Xiaomi"
            elif "moto" in tokens or "razr" in tokens:
                resolved_brand = "Motorola"

    colour_words = set(_COLOUR_WORDS)
    if colour:
        colour_words |= set(re.sub(r"[^a-z0-9 ]", " ", colour.lower()).split())

    # Apple's phones have a parser of their own that already reads every shorthand the
    # trade uses ('15PM', 'IP 15 Pro Max', 'iphone15promax', '16e').
    if _IPHONE.search(body) or (
        resolved_brand == "Apple" and _APPLE_NUMBER.search(body) and not _APPLE_OTHER.search(body)
    ):
        iphone = normalise_model(str(text))
        if iphone:
            model = re.sub(r"[^a-z0-9]", "", iphone.lower())
            return CanonicalModel(
                brand="Apple", key=f"apple:{model}", model=model,
                tokens=tuple(iphone.lower().split()), category="phone",
                network=network, connectivity=None, confident=True,
            )

    kept: list[str] = []
    optional = _OPTIONAL_LINE_WORDS.get(resolved_brand or "", set())
    for token in tokens:
        if token in _MAKER_TOKENS or token in optional:
            continue
        if token in _NOISE or token in colour_words:
            continue
        kept.append(token)

    if resolved_brand == "Samsung":
        kept = _samsung(kept)

    if resolved_brand is None:
        resolved_brand = _brand_from_shape(kept)
        if resolved_brand == "Samsung":
            kept = _samsung(kept)

    model = "".join(kept)
    if not model:
        return CanonicalModel(brand=resolved_brand, network=network, connectivity=connectivity)

    category = _category_from_shape(kept, resolved_brand)
    has_number = any(any(c.isdigit() for c in t) for t in kept)
    confident = bool(resolved_brand) and (has_number or len(kept) >= 2)

    slug = (resolved_brand or "").lower().replace(" ", "").replace("-", "")
    return CanonicalModel(
        brand=resolved_brand,
        key=f"{slug}:{model}",
        model=model,
        tokens=tuple(kept),
        category=category,
        network=network,
        connectivity=connectivity,
        confident=confident,
    )


_SAMSUNG_CODE = re.compile(r"^(?:sm)?[a-z]{1,2}\d{3}[a-z0-9]{0,3}$")


def _samsung(tokens: list[str]) -> list[str]:
    """Samsung's own naming habits, written one way.

    'Z' before Fold and Flip is dropped (Galaxy Z Fold7 = Fold 7); 'Edge' after an S
    number is the 'E' Samsung prints on the box (S25 Edge = S25E). Part codes --
    S938, A176B, SM-X130 -- are dropped when the line also names the model, because a
    buyer writing 'S25 Ultra S938' and a supplier writing 'S25 Ultra (S938)' mean one
    phone. Samsung's model names carry one or two digits (S25, A56); codes carry three.
    """
    named = any(re.fullmatch(r"[a-z]{1,2}\d{1,2}|\d{1,2}", t) for t in tokens)
    if named:
        tokens = [t for t in tokens if t != "sm" and not _SAMSUNG_CODE.match(t)]
    out: list[str] = []
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token == "z" and following in ("fold", "flip"):
            continue
        if token == "edge" and out and re.fullmatch(r"s\d{2}", out[-1]):
            out.append("e")
            continue
        # 'S24U' is how half the trade writes the S24 Ultra.
        if token == "u" and out and re.fullmatch(r"s\d{2}", out[-1]):
            out.append("ultra")
            continue
        out.append(token)
    return out


_SAMSUNG_SHAPE = re.compile(r"^(?:[samf]\d{2,3}[a-z]?|fold|flip|tab|galaxy|xcover|buds\d*)$")


def _brand_from_shape(tokens: list[str]) -> str | None:
    """A brand for a line that names none, from the model's own shape.

    Only shapes that belong to one maker: an S/A/M/F number followed by nothing else
    that looks like another maker's line ('S23', 'A56', 'Fold 7'), 'Pixel', 'iPad'.
    Anything less certain stays None -- the model fallback is cheaper than a wrong brand.
    """
    if not tokens:
        return None
    first = tokens[0]
    if first in ("pixel",):
        return "Google"
    if first in ("ipad", "airpods", "macbook", "imac", "airtag"):
        return "Apple"
    if first in ("redmi", "poco"):
        return "Xiaomi"
    if first in ("razr",):
        return "Motorola"
    if first in ("nord",):
        return "OnePlus"
    if _SAMSUNG_SHAPE.match(first) and (first not in ("tab", "buds") or len(tokens) > 1):
        if first in ("fold", "flip") and not (len(tokens) > 1 and tokens[1].isdigit()):
            return None
        return "Samsung"
    return None


def _category_from_shape(tokens: list[str], brand: str | None) -> str | None:
    """A category the model's shape makes certain. None whenever it does not."""
    joined = " ".join(tokens)
    if re.search(r"\b(?:case|cover|folio|keyboard|pencil|pen|charger|adapter|cable|strap|"
                 r"protector|glass|mouse|stand|holder)\b", joined):
        return "accessory"
    if re.search(r"\b(?:buds\d*|earbuds?|headphones?|headset|speaker|airpods)\b", joined):
        return "audio"
    if re.search(r"\b(?:watch|band)\b", joined):
        return "wearable"
    if re.search(r"\b(?:tab|ipad|pad|tablet|magicpad)\b|\bpad\s*\w*\d|magicpad", joined):
        return "tablet"
    # One digit too, once the brand is known: the Galaxy S9 still trades.
    if brand == "Samsung" and re.match(r"^(?:[samf]\d{1,3}[a-z]?|fold|flip|xcover)\b", joined):
        return "phone"
    if brand == "Google" and joined.startswith("pixel") and "watch" not in joined:
        return "phone"
    return None
