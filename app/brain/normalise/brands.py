"""Brand and category detection.

The samples cover Apple, Samsung, Google, Xiaomi, Motorola, Sony, Nintendo, Honor,
Oppo, OnePlus and a long tail of accessory makers — plus televisions, consoles, power
banks and, in one list, hand sanitiser. Brand is therefore the first thing extraction
establishes, because it decides which attributes even apply: a car charger has no
storage capacity, and a PlayStation has no region code.
"""

import re

# Ordered: longer and more specific aliases first, so 'galaxy tab' is not eaten by
# 'galaxy', and 'redmi' resolves to Xiaomi rather than being missed.
_BRAND_ALIASES: list[tuple[str, str]] = [
    (r"\b(?:iphone|ipad|airpods?|airtag|macbook|imac|apple\s*watch|magsafe|apple)\b", "Apple"),
    # Apple's watches, written the way suppliers actually write them. A whole section of
    # one sample list reads 'Watch Ultra 3 LTE 49mm Black Titanium Case' with the word
    # Apple nowhere on the line — 40-odd rows that matched nothing, because 'apple watch'
    # above needs both words. 'Watch' alone is far too broad, so each model line is named.
    (r"\bwatch\s*(?:ultra|series|se)\b", "Apple"),
    # Beats is Apple, and their lists never say so.
    (r"\b(?:beats|powerbeats|solo\s*buds|studio\s*buds)\b", "Apple"),
    # SanDisk product lines. Their flash drives are sold by line name — 'Ultra Flair',
    # 'Cruzer Blade', 'Extreme Pro' — and the manufacturer is rarely written at all.
    # Safe after the Apple line above, which claims 'Watch Ultra' first: a flash drive
    # named 'Ultra' cannot be reached until 'Watch Ultra' has had its turn.
    (
        r"\b(?:sandisk|cruzer|extreme\s*pro)\b"
        r"|\bultra\b[^\n]{0,28}\bflash\s*drive\b"
        r"|\b(?:ultra|extreme)\s*(?:flair|fit|dual|shift|luxe|go)\b",
        "SanDisk",
    ),
    # Samsung part codes. Whole lists arrive grouped under a 'Samsung' heading with the
    # brand named nowhere on the individual lines — 'A17 5G DS SM-A176 4+128 — Black'.
    # SM- is unambiguous, so it recovers the brand without needing the heading.
    (r"\bSM[\s-]?[A-Z]\d{3}", "Samsung"),
    (r"\b(?:samsung|galaxy)\b", "Samsung"),
    (r"\b(?:google\s*pixel|pixel|google)\b", "Google"),
    (r"\b(?:xiaomi|redmi|poco|mi\s*band)\b", "Xiaomi"),
    # Each alternative carries its own boundaries. A single trailing \b never matches
    # after 'Mot.' — a full stop and the following space are both non-word characters,
    # so there is no transition for \b to sit on, and the abbreviation silently failed.
    (r"(?:\bmotorola\b|\bmot\.|\bmoto\s*[ge]\b|\bmoto\b|\brazr\b)", "Motorola"),
    (r"\bninja\b", "Ninja"),
    (r"\b(?:nothing\s*(?:phone|watch|ear)|nothing)\b", "Nothing"),
    (r"\b(?:dyson|airwrap|supersonic)\b", "Dyson"),
    (r"\bgopro\b", "GoPro"),
    (r"\b(?:dji|osmo|mavic|ronin)\b", "DJI"),
    (r"\bmarshall\b", "Marshall"),
    (r"\b(?:amazon|echo\s*(?:dot|spot|show)|fire\s*(?:hd|tv)|kindle|alexa)\b", "Amazon"),
    (r"\bdreame\b", "Dreame"),
    (r"\bcat\s*s\d{2}\b", "CAT"),
    (r"\b(?:bosch|philips|braun)\b", "Other"),
    (r"\b(?:oneplus|one\s*plus)\b", "OnePlus"),
    # Sony's own part codes, which is how their audio and phones are listed. WH- and WF-
    # are headphones and earbuds; XQ- is an Xperia. The brand name itself rarely appears.
    (
        r"\b(?:playstation|ps5|ps4|sony|bravia|xperia)\b"
        r"|\b(?:WH|WF)-[A-Z0-9]{4,}"
        r"|\bXQ-[A-Z]{2}\d{2}",
        "Sony",
    ),
    (r"\b(?:nintendo|switch\s*2|switch)\b", "Nintendo"),
    (r"\b(?:huawei|honor)\b", "Honor"),
    (r"\boppo\b", "Oppo"),
    (r"\bvivo\b", "Vivo"),
    (r"\brealme\b", "Realme"),
    (r"\bnokia\b", "Nokia"),
    (r"\b(?:microsoft|xbox|surface)\b", "Microsoft"),
    (r"\b(?:lenovo|thinkpad)\b", "Lenovo"),
    (r"\basus\b", "Asus"),
    (r"\bacer\b", "Acer"),
    (r"\b(?:hewlett|hp)\b", "HP"),
    (r"\bdell\b", "Dell"),
    (r"\btcl\b", "TCL"),
    (r"\blg\b", "LG"),
    (r"\bjbl\b", "JBL"),
    (r"\banker\b", "Anker"),
    (r"\bbelkin\b", "Belkin"),
    (r"\b4smarts\b", "4smarts"),
    (r"\bamazfit\b", "Amazfit"),
    (r"\bgarmin\b", "Garmin"),
]

_BRANDS = [(re.compile(p, re.IGNORECASE), name) for p, name in _BRAND_ALIASES]

# Category is checked before brand-specific parsing, because it decides which
# attributes apply. Accessory patterns come first: 'Case for iPhone 11' is an
# accessory, not a phone, and the word iPhone in it is describing compatibility.
_CATEGORY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:case|cover|sleeve|screen\s*protector|tempered\s*glass|folio)\b", "accessory"),
    (r"\b(?:charger|cable|adapter|power\s*bank|powerbank|battery\s*pack|magsafe|dock)\b",
     "accessory"),
    (r"\b(?:airpods?|earbuds?|headphones?|headset|speaker|soundbar|buds)\b", "audio"),
    (r"\b(?:watch|band|amazfit|smartwatch)\b", "wearable"),
    (r"\b(?:playstation|ps5|ps4|xbox|switch|console|controller|dualsense)\b", "console"),
    (r"\b(?:ipad|tab\b|tablet)\b", "tablet"),
    (r"\b(?:tv|dled|qled|oled\s*tv|smart\s*tv|television)\b", "tv"),
    (r"\b(?:laptop|notebook|macbook|thinkpad|chromebook)\b", "computer"),
    # No outer \b on this one: 'Galaxy A56' would fail a trailing boundary after
    # matching only 'Galaxy A5', which silently left every Samsung phone uncategorised.
    (r"(?:\biphone\b|\bgalaxy\s*[sazfm]?\d+|\bgalaxy\s*z\s*(?:flip|fold)"
     r"|\bpixel\s*\d+|\bsmartphone\b|\bredmi\b|\bmoto\s*[ge]\b|\bxcover\b)", "phone"),
    (r"\b(?:mask|kn95|ffp2|hand\s*gel|sanitiser|sanitizer|antigen|test\s*kit)\b", "other"),
]

_CATEGORIES = [(re.compile(p, re.IGNORECASE), name) for p, name in _CATEGORY_PATTERNS]


# Bare Samsung series codes: A356B, S931B, S731, F966, G556B. Used only as a fallback,
# and only when the line also carries Samsung-style notation — '5G', 'DS', 'Ent. Ed.'
# or the '8+256' RAM-plus-storage form.
#
# The gate matters. 'M404' is an HP printer and 'A356' could be a part number from
# anyone; on its own the pattern would attach Samsung to other brands' stock. This is a
# deliberately narrow patch, not the real answer — lists like Automic's group products
# under a 'Samsung' heading and name no brand on the individual lines, and inheriting
# the section heading is the general fix. That belongs in the extractor.
_SAMSUNG_CODE = re.compile(r"\b[ASFGZN]\d{3}[A-Z]?\b")
_SAMSUNG_CONTEXT = re.compile(
    r"\b(?:5g|lte|ds|duos|ent\.?\s*ed\.?)\b|\b\d{1,2}\s*\+\s*\d{2,4}\b", re.IGNORECASE
)


def detect_brand(text: str | None) -> str | None:
    if not text:
        return None

    for pattern, name in _BRANDS:
        if pattern.search(text):
            return name

    if _SAMSUNG_CODE.search(text) and _SAMSUNG_CONTEXT.search(text):
        return "Samsung"

    return None


def detect_category(text: str | None) -> str | None:
    """Best-effort category. None is acceptable — category filters the board, it is
    not part of identity, so an unknown one costs a filter rather than a mismatch."""
    if not text:
        return None
    for pattern, name in _CATEGORIES:
        if pattern.search(text):
            return name
    return None
