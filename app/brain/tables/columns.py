"""Find the header row, and work out what its columns mean.

Suppliers write headers in German, Italian, Polish, Dutch, Romanian, Spanish and
English, and rarely put them in row 1. SMTR opens with seven rows of greeting and
trading terms; SELTE has a blank row, then headers, then brand separator rows.

Both problems are solved by scoring rather than by assuming a position: every row in
the first stretch of the sheet is scored on how many of its cells look like column
names, and the best-scoring row wins. A table whose header cannot be recognised at all
returns None, which is the one case that needs a model — once per table, not per row.
"""

import re
from dataclasses import dataclass, field

# Header vocabulary across the languages in the sample. Matching is on a normalised
# form: lowercased, punctuation stripped, so 'Preis / Price €' and 'preis price' agree.
_ALIASES: dict[str, tuple[str, ...]] = {
    "description": (
        "description", "descriptions", "artikelbezeichnung", "bezeichnung", "modell",
        "model", "models", "product", "produkt", "produkte", "descrizione", "descripcion",
        "omschrijving", "item", "items", "article", "articolo", "nazwa", "opis",
        "denumire", "produs", "designation", "typ", "type", "name",
    ),
    "ean": (
        "ean", "eannummer", "ean nummer", "gtin", "barcode", "barcodes",
        "kod ean", "codice ean",
    ),
    "quantity": (
        "qty", "quantity", "qty available", "available", "stock", "in stock",
        "lagerbestand", "lagerbestand qty", "bestand", "menge", "anzahl", "ready",
        "incoming", "disponibile", "disponibili", "quantita", "cantidad", "aantal",
        "voorraad", "ilosc", "stan", "stoc", "pcs", "pieces", "units",
    ),
    "price": (
        "price", "prices", "preis", "preise", "prezzo", "precio", "prix", "prijs",
        "cena", "pret", "unit price", "price eur", "prezzo eur", "preis price",
        "price exw", "exw", "netto", "net price", "vk", "ek",
    ),
    "brand": (
        "brand", "brands", "marke", "marca", "merk", "marka", "manufacturer",
        "hersteller", "producent", "make",
    ),
    "code": (
        "code", "codes", "partcode", "part code", "part no", "part number", "p n", "pn",
        "artikelnummer", "artnr", "sku", "mpn", "ref", "reference", "codice", "kod",
    ),
    "category": ("category", "categories", "kategorie", "categoria", "categorie", "kategoria"),
    "currency": ("currency", "waehrung", "wahrung", "valuta", "moneda", "devise"),
    "colour": ("colour", "color", "colours", "colors", "farbe", "kleur", "colore", "kolor"),
    "notes": ("notes", "note", "obs", "remarks", "bemerkung", "specifications", "spec", "link"),
}

# Some suppliers head the price column with nothing but the currency — Masterfone uses
# 'USD', Motorola's list has both 'USD' and 'EUR'. Read literally that is a currency
# column with no price anywhere, and the whole 2,235-row table becomes unparseable.
_CURRENCY_ONLY = {
    "usd": "USD", "eur": "EUR", "gbp": "GBP", "aed": "AED", "pln": "PLN",
    "chf": "CHF", "czk": "CZK", "huf": "HUF", "ron": "RON", "sek": "SEK",
    "€": "EUR", "$": "USD", "£": "GBP",
}

_LOOKUP: dict[str, str] = {
    alias: field_name for field_name, aliases in _ALIASES.items() for alias in aliases
}

# A currency symbol or code in the header names the column's currency — 'Preis / Price €',
# 'Prezzo EUR'. Reliable, and it removes the guesswork from every row beneath.
_HEADER_CURRENCY = [
    (re.compile(r"€|\beur\b", re.IGNORECASE), "EUR"),
    (re.compile(r"\$|\busd\b", re.IGNORECASE), "USD"),
    (re.compile(r"£|\bgbp\b", re.IGNORECASE), "GBP"),
    (re.compile(r"\baed\b|\bdhs?\b", re.IGNORECASE), "AED"),
    (re.compile(r"\bpln\b|\bzł\b", re.IGNORECASE), "PLN"),
]

_PUNCT = re.compile(r"[^\w\s€$£]", re.UNICODE)


@dataclass
class ColumnMap:
    """Which column index carries which field."""

    columns: dict[str, int] = field(default_factory=dict)
    header_row: int = 0
    currency: str | None = None

    def get(self, field_name: str) -> int | None:
        return self.columns.get(field_name)

    @property
    def score(self) -> int:
        return len(self.columns)

    @property
    def is_usable(self) -> bool:
        """A table is parseable if we know what is being traded, and one fact about it.

        Price *or* quantity, and this took a real list to get right. A buyer's
        requirement list has no prices in it — being quoted is the entire reason they
        sent it. Thaysen's WTB sheet is headed ['QTY', 'MODELL'] and Bauer's asks for
        'iPhone 17 Pro 256GB, mixed colors' against 'Please offer'. Demanding a price
        column discarded every one of them, which is to say all of the demand the
        matching engine exists to fill.

        Everything else can be recovered from the description text.
        """
        if "description" not in self.columns:
            return False
        return "price" in self.columns or "quantity" in self.columns


def normalise_header(cell: str | None) -> str:
    if not cell:
        return ""
    text = str(cell).replace("\n", " ").replace("/", " ")
    text = _PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def classify_header_cell(cell: str | None) -> str | None:
    """Which field a header cell names, or None."""
    text = normalise_header(cell)
    if not text:
        return None

    if text in _CURRENCY_ONLY:
        return "price"

    if text in _LOOKUP:
        return _LOOKUP[text]

    # 'Preis / Price €' normalises to 'preis price ' — match on the words present.
    words = text.split()
    for word in words:
        if word in _LOOKUP:
            return _LOOKUP[word]

    for alias, field_name in _LOOKUP.items():
        if " " in alias and alias in text:
            return field_name

    return None


def detect_header_row(grid: list[list[str]], search_depth: int = 30) -> int | None:
    """Find the header row by scoring, not by position.

    SMTR's sheet opens with seven rows of greeting and trading terms before the real
    header. Assuming row 0 would parse the greeting as column names and then read every
    product row against the wrong fields — which produces plausible-looking rows with
    prices in the quantity column, and no error anywhere.
    """
    best_index: int | None = None
    best_score = 0

    for index, row in enumerate(grid[:search_depth]):
        fields = {classify_header_cell(cell) for cell in row}
        fields.discard(None)
        score = len(fields)

        # A header names several different things. One matching cell is a coincidence —
        # a row containing the single word 'Price' is more likely a label than a header.
        if score >= 2 and score > best_score:
            best_score = score
            best_index = index

    return best_index


def map_columns(header: list[str]) -> ColumnMap:
    """Map a header row to column indices.

    First match wins for each field: sheets often repeat a concept — 'Ready' and
    'Incoming' are both quantities — and the leftmost is the one that means stock on
    hand rather than stock on its way.
    """
    columns: dict[str, int] = {}
    currency: str | None = None

    for index, cell in enumerate(header):
        field_name = classify_header_cell(cell)
        if field_name and field_name not in columns:
            columns[field_name] = index

        if currency is None and cell:
            normalised = normalise_header(cell)
            if normalised in _CURRENCY_ONLY:
                currency = _CURRENCY_ONLY[normalised]
            else:
                for pattern, code in _HEADER_CURRENCY:
                    if pattern.search(str(cell)):
                        currency = code
                        break

    return ColumnMap(columns=columns, currency=currency)


def looks_like_header(row: list[str], minimum: int = 2) -> bool:
    """Whether a row inside the body is a repeated header.

    GOtel's list reprints its header every few rows as a visual separator. Parsed as
    data those become offers named 'EAN' priced at 'Price'.
    """
    fields = {classify_header_cell(cell) for cell in row}
    fields.discard(None)
    return len(fields) >= minimum


def infer_description_column(
    grid: list[list[str]], header_index: int, taken: set[int], sample: int = 40
) -> int | None:
    """Find the description column when its header cell is blank.

    GOtel's header is ['', 'EAN', 'Price', 'Quantity', ...] — every column is labelled
    except the one naming the product. The column is identifiable from its contents
    instead: mostly text, rarely a bare number.
    """
    rows = grid[header_index + 1 : header_index + 1 + sample]
    if not rows:
        return None

    width = max((len(r) for r in rows), default=0)
    best: tuple[int, float] | None = None

    for index in range(width):
        if index in taken:
            continue
        values = [r[index].strip() for r in rows if index < len(r) and r[index].strip()]
        if len(values) < 2:
            continue

        wordy = sum(1 for v in values if is_wordy(v))
        ratio = wordy / len(values)
        if ratio >= 0.7 and (best is None or ratio > best[1]):
            best = (index, ratio)

    return best[0] if best else None


def is_wordy(value: str) -> bool:
    """Text with letters and some length — a product name, not a code or a number."""
    if len(value) < 6:
        return False
    letters = sum(1 for c in value if c.isalpha())
    return letters >= 4 and letters / len(value) > 0.4
