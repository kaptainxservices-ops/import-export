"""Deterministic normalisation. No LLM calls in this package, ever.

Extraction pulls messy strings out of an email. This package turns them into the
canonical values that identity, filtering and matching depend on. It is deliberately
plain Python for three reasons: it is the hot path for every row of a 500-line price
list, it must give the same answer every time, and a model cannot be unit tested to
four decimal places.

Two rules hold throughout.

**Never guess.** Every function returns None when it does not know, and the raw string
is always preserved alongside. A None lands the row in review, where a human resolves
it in seconds. A guess silently corrupts a price the client then trades on.

**Normalisation never raises.** One malformed cell in row 300 of a spreadsheet must not
abandon the other 499 rows.
"""

from app.brain.normalise.brands import detect_brand, detect_category
from app.brain.normalise.colours import normalise_colour
from app.brain.normalise.grades import normalise_grade
from app.brain.normalise.models import normalise_model
from app.brain.normalise.money import (
    detect_declared_currency,
    detect_incoterm,
    detect_price_basis,
    detect_vat_included,
    find_price,
    normalise_currency,
    parse_price,
    strip_marked_prices,
)
from app.brain.normalise.product import ProductSpec, parse_product
from app.brain.normalise.quantity import parse_quantity
from app.brain.normalise.regions import normalise_region_code
from app.brain.normalise.storage import normalise_storage_gb
from app.brain.normalise.variants import Variant, expand_variants

__all__ = [
    "ProductSpec",
    "Variant",
    "detect_brand",
    "detect_category",
    "detect_declared_currency",
    "detect_incoterm",
    "detect_price_basis",
    "detect_vat_included",
    "expand_variants",
    "find_price",
    "normalise_colour",
    "normalise_currency",
    "normalise_grade",
    "normalise_model",
    "normalise_region_code",
    "normalise_storage_gb",
    "parse_price",
    "parse_product",
    "parse_quantity",
    "strip_marked_prices",
]
