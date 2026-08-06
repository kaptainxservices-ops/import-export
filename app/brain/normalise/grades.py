"""Grades: the hardest field, and the one we have least authority over.

'A' from one supplier is not 'A' from another. There is no industry standard, so the
mapping is not hardcoded here — it comes from the `grades` and `grade_aliases` tables,
which the client owns and edits. This module only does two things: tidy the raw string
into a comparable key, and look it up.

An unknown grade returns None. That sends the row to review rather than inventing a
condition for goods worth hundreds of dollars a unit.
"""

import re
from collections.abc import Mapping

# Words that decorate a grade without changing it.
_FILLER = re.compile(
    r"\b(?:grade|graded|cond|condition|quality|class|stock|units?|phones?)\b",
    re.IGNORECASE,
)
_NOISE = re.compile(r"[()\[\].,:;\"']")


def grade_key(raw: str | None) -> str | None:
    """Reduce a written grade to a comparable lookup key.

    'A+ grade', '(A+)', 'GRADE  A+' all reduce to 'a+'.
    """
    if not raw:
        return None

    text = _NOISE.sub(" ", raw)
    text = _FILLER.sub(" ", text)
    text = re.sub(r"[-_]", " ", text)
    text = re.sub(r"\s*\+\s*", "+", text)   # 'A +' and 'A+' agree
    text = re.sub(r"\s+", " ", text).strip().lower()

    return text or None


def normalise_grade(
    raw: str | None,
    alias_map: Mapping[str, str] | None = None,
) -> str | None:
    """Map a raw grade onto the tenant's canonical scale.

    `alias_map` is keyed by `grade_key()` output and comes from the database — the
    sender-specific aliases first, then the tenant-wide fallback. Pass None and the
    function will only recognise a value that is already canonical.
    """
    key = grade_key(raw)
    if key is None:
        return None

    if alias_map:
        lowered = {k.lower(): v for k, v in alias_map.items()}
        if key in lowered:
            return lowered[key]

    # A bare, well-formed grade token is allowed through unchanged: 'A+', 'B', 'C'.
    # Anything wordier ('14 day', 'CPO', 'as-is') means something specific to a
    # particular supplier and must be mapped explicitly, not assumed.
    if re.fullmatch(r"[a-e][+\-]?", key):
        return key.upper()

    return None
