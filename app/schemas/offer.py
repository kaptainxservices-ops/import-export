"""What extraction produces: one canonical line item.

Three decisions are baked in here and are worth stating plainly.

1. **The unit is the line item, not the email.** One message can carry a single lot or
   a 500-row catalogue, so the email is never the record.

2. **Raw strings are kept alongside canonical values.** When the client says a grade
   is wrong, the argument is settled by showing exactly what the sender wrote. It also
   means normalisation rules can be re-run over old data without losing the source.

3. **Identity excludes price and quantity** — see `identity_key()`. Those two fields are
   precisely what changes day to day; including them would make every reprice look like
   a brand new offer and break the daily reconciliation.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Side = Literal["sell", "buy"]
PriceBasis = Literal["per_unit", "per_lot", "unknown"]


class ExtractedLineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    side: Side = Field(description="'sell' = a seller's offer, 'buy' = a buyer's requirement")

    # --- Canonical spec ---
    model: str | None = Field(default=None, description="e.g. 'iPhone 15 Pro Max'")
    storage_gb: int | None = None
    colour: str | None = None
    grade: str | None = Field(default=None, description="Canonical grade after per-sender mapping")
    region_code: str | None = Field(default=None, description="LL/A, ZP/A, CH/A, J/A …")

    # --- Commercials ---
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = Field(default=None, description="ISO 4217, e.g. USD, AED")
    price_basis: PriceBasis = "unknown"
    incoterm: str | None = Field(default=None, description="EXW, DDP, FOB …")
    vat_included: bool | None = None
    location: str | None = Field(default=None, description="Where the goods are")

    notes: str | None = None

    # --- Provenance ---
    raw: dict[str, str] = Field(
        default_factory=dict,
        description="Exactly what the sender wrote, per field, before normalisation",
    )
    source_ref: str | None = Field(
        default=None,
        description="Where in the source this came from: a line number, or 'Sheet1!A14'",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    def identity_key(self) -> tuple:
        """Stable identity for daily reconciliation.

        Same lot at a new price is the SAME offer updated, not a new row — so price and
        quantity are deliberately absent from this tuple.
        """
        return (
            self.side,
            (self.model or "").lower(),
            self.storage_gb,
            (self.colour or "").lower(),
            (self.grade or "").lower(),
            (self.region_code or "").lower(),
        )


class ExtractionResult(BaseModel):
    """Everything one email yielded, plus what it took to get it."""

    model_config = ConfigDict(extra="ignore")

    message_id: str
    classification: str
    items: list[ExtractedLineItem] = Field(default_factory=list)

    is_complete_list: bool | None = Field(
        default=None,
        description="True if this looks like the sender's full current stock. Only when "
        "True may absence of a previously seen item be read as sold. None means "
        "undecided, which must close nothing.",
    )

    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = Field(default_factory=list)
