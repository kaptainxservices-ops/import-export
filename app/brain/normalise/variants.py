"""One written line is frequently several offers.

    A17 LTE DS SM-A175 4+128 — Black / Blue / Grey — €125      3 offers, one price
    A37 SM-A376B 5G 6+128 — Lavender €200 · Charcoal €195      2 offers, two prices
    IPHONE 15 128GB black / blue / pink            (qty 100)   3 requests

Expanding these is not cosmetic. The safety check that stops a bad parse from wiping a
supplier's stock compares today's row count against that supplier's usual count, so a
line that collapses to one row today and three tomorrow makes that check misfire.

Expansion is conservative by design: when a line cannot be split confidently it is
returned whole, as a single variant. An unexpanded line is a row a human can fix. A
wrongly split one invents stock that does not exist.
"""

import re
from dataclasses import dataclass

from app.brain.normalise.product import _COLOUR_WORDS

# Separates whole variants that each carry their own price.
_VARIANT_SPLIT = re.compile(r"\s*[·•;]\s*|\s+\|\s+")

# A price with a currency marker, which is what makes a segment self-contained.
_PRICED = re.compile(r"(?:[€$£]\s?\d[\d.,]*|\d[\d.,]*\s?(?:€|eur|usd|gbp)\b)", re.IGNORECASE)

# 'Black / Blue / Grey' or 'black, blue, pink'.
#
# Built from the colour vocabulary rather than by splitting on slashes. Splitting first
# and checking afterwards fails on a real line: in
# 'SM-A175 4+128 — Black / Blue / Grey — €125' the first piece greedily swallows the
# whole model number, so the run stops looking like colours and never expands.
_COLOUR_ALT = "|".join(sorted((re.escape(c) for c in _COLOUR_WORDS), key=len, reverse=True))
_COLOUR_PHRASE = rf"(?:\b(?:{_COLOUR_ALT})\b\s*){{1,3}}"
_ALTERNATIVES = re.compile(
    rf"{_COLOUR_PHRASE}(?:[/,]\s*{_COLOUR_PHRASE})+", re.IGNORECASE
)


@dataclass(frozen=True)
class Variant:
    """One product line's worth of text, plus the colour it applies to."""

    text: str
    colour: str | None = None
    expanded_from_colours: bool = False


def expand_variants(line: str | None) -> list[Variant]:
    """Split a line into the offers it actually represents."""
    if not line or not line.strip():
        return []

    text = re.sub(r"\s+", " ", line).strip()

    segments = _split_priced_segments(text)

    out: list[Variant] = []
    for segment in segments:
        out.extend(_expand_colours(segment))
    return out or [Variant(text=text)]


def _split_priced_segments(text: str) -> list[str]:
    """Split on '·' only when the pieces genuinely look like separate priced variants.

    Trailing segments carry no product description — 'Charcoal €195' on its own is
    meaningless — so the description from the first segment is prefixed onto them.
    """
    parts = [p.strip() for p in _VARIANT_SPLIT.split(text) if p.strip()]
    if len(parts) < 2:
        return [text]

    priced = [p for p in parts if _PRICED.search(p)]
    if len(priced) < 2:
        return [text]

    base = _description_prefix(parts[0])
    segments = [parts[0]]
    for part in parts[1:]:
        segments.append(f"{base} {part}".strip() if base and not _has_description(part) else part)
    return segments


def _description_prefix(segment: str) -> str:
    """Everything before the colour-and-price tail of the first segment."""
    split = re.split(r"\s+[-–—]\s+", segment)
    if len(split) > 1:
        return split[0].strip()

    match = _PRICED.search(segment)
    if not match:
        return segment.strip()

    head = segment[: match.start()].strip()
    words = head.split()
    while words and words[-1].lower().strip(",/") in _COLOUR_WORDS:
        words.pop()
    return " ".join(words)


def _has_description(segment: str) -> bool:
    """A segment is self-contained if it has words that are not colours or prices."""
    stripped = _PRICED.sub(" ", segment)
    words = [w.lower().strip(".,/-") for w in stripped.split()]
    meaningful = [w for w in words if w and w not in _COLOUR_WORDS]
    return len(meaningful) >= 2


def _expand_colours(segment: str) -> list[Variant]:
    """One variant per colour when the line offers a colour list at a single price."""
    match = _find_colour_alternatives(segment)
    if not match:
        return [Variant(text=segment)]

    span, colours = match
    before = segment[: span[0]].rstrip()
    after = segment[span[1] :].lstrip()

    return [
        Variant(
            text=f"{before} {colour} {after}".strip(),
            colour=colour,
            expanded_from_colours=True,
        )
        for colour in colours
    ]


def _find_colour_alternatives(segment: str) -> tuple[tuple[int, int], list[str]] | None:
    """Locate a run like 'Black / Blue / Grey' where every element is a colour.

    Requiring *every* element to be a colour is what keeps this safe. It means
    'Blue / 256GB' and 'iPhone 15 / 16' are left alone rather than split into products
    that were never offered.
    """
    best: tuple[tuple[int, int], list[str]] | None = None

    for match in _ALTERNATIVES.finditer(segment):
        elements = [e.strip() for e in re.split(r"[/,]", match.group(0)) if e.strip()]
        if len(elements) < 2:
            continue
        if not all(_is_colour_phrase(e) for e in elements):
            continue
        elements = [_titlecase(e) for e in elements]
        if best is None or len(elements) > len(best[1]):
            best = ((match.start(), match.end()), elements)

    return best


def _titlecase(text: str) -> str:
    return " ".join(word.capitalize() for word in text.split())


def _is_colour_phrase(text: str) -> bool:
    words = [w.lower().strip(".,()") for w in text.split()]
    words = [w for w in words if w]
    if not words or len(words) > 3:
        return False
    return all(w in _COLOUR_WORDS for w in words)
