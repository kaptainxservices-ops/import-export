"""Run the normalisers over the real sample emails and report hit rates.

Unit tests prove the code does what I expected. This proves what it does on the actual
inbox, which is a different question — and the one that decides whether the product
works. It reports coverage rather than pass/fail, because there is no ground truth to
compare against yet; the numbers are for judging where the remaining gaps are.

    python scripts/check_samples.py samples/
"""

from __future__ import annotations

import email
import re
import sys
from collections import Counter
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brain.normalise import (  # noqa: E402
    expand_variants,
    parse_price,
    parse_product,
)
from app.brain.sender import resolve_sender  # noqa: E402
from app.brain.tables import parse_grid, read_html_tables, read_spreadsheet  # noqa: E402

INTERNAL_DOMAINS = {"tvdservices.com"}
INTERNAL_ADDRESSES = {"tvdservices@hotmail.com", "tvdlogistics@outlook.com"}

PRICE_LIKE = re.compile(r"\d[\d.,]*")
NOISE_LINE = re.compile(
    r"unsubscribe|privacy|do not repl|please reply|best regards|kind regards|"
    r"skype|whatsapp|mailto:|http|^\s*$",
    re.IGNORECASE,
)


def body_parts(msg) -> tuple[str, str]:
    text, html = "", ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_filename():
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if part.get_content_type() == "text/plain" and not text:
            text = content
        elif part.get_content_type() == "text/html" and not html:
            html = content
    return text, html


def attachments(msg) -> list[tuple[str, bytes]]:
    out = []
    for part in msg.walk():
        name = part.get_filename()
        if not name or not name.lower().endswith((".xlsx", ".xls", ".xlsm")):
            continue
        payload = part.get_payload(decode=True)
        if payload:
            out.append((name, payload))
    return out


def prose_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip(" •-\t")
        if len(line) < 8 or NOISE_LINE.search(line):
            continue
        out.append(line)
    return out


def main(root: Path) -> int:
    files = sorted(root.rglob("*.eml"))
    if not files:
        print(f"no .eml files under {root}")
        return 1

    methods: Counter[str] = Counter()
    needs_review: list[str] = []
    stats: Counter[str] = Counter()
    unbranded: list[str] = []
    unmapped: list[str] = []

    for path in files:
        with path.open("rb") as fh:
            msg = email.message_from_binary_file(fh, policy=policy.default)

        text, html = body_parts(msg)
        resolved = resolve_sender(
            msg.get("From"), msg.get("Subject"), text or html,
            internal_domains=INTERNAL_DOMAINS,
            internal_addresses=INTERNAL_ADDRESSES,
        )
        methods[resolved.method] += 1
        if resolved.needs_review:
            needs_review.append(f"{path.name[:48]:48} -> {resolved.name or resolved.email or '?'}")

        grids = read_html_tables(html)
        for name, payload in attachments(msg):
            grids.extend(read_spreadsheet(payload, name))

        structured_rows = 0
        for grid in grids:
            table = parse_grid(grid)
            if table.needs_column_mapping:
                stats["tables_needing_mapping"] += 1
                unmapped.append(f"{path.name[:40]:40} {grid.source} ({len(grid)} rows)")
                continue

            stats["tables_parsed"] += 1
            for row in table.rows:
                structured_rows += 1
                stats["rows"] += 1
                if row.ean:
                    stats["ean"] += 1
                if row.price is not None:
                    stats["price"] += 1
                if row.quantity is not None:
                    stats["quantity"] += 1
                if row.brand:
                    stats["brand"] += 1
                elif len(unbranded) < 12:
                    unbranded.append(row.description[:80])
                if row.section:
                    stats["from_section"] += 1

                spec = parse_product(row.description, ean=row.ean)
                if spec.capacity_gb:
                    stats["capacity"] += 1
                if spec.category:
                    stats["category"] += 1

        # Prose emails have no tables at all; those still go through line parsing.
        if structured_rows == 0:
            for line in prose_lines(text)[:400]:
                for variant in expand_variants(line):
                    spec = parse_product(variant.text)
                    stats["prose_lines"] += 1
                    if spec.brand:
                        stats["prose_brand"] += 1
                    if PRICE_LIKE.search(variant.text) and parse_price(variant.text) is not None:
                        stats["prose_price"] += 1
                    if variant.expanded_from_colours:
                        stats["expanded"] += 1

    print(f"\n{'=' * 68}\nSENDER RESOLUTION — {len(files)} emails\n{'=' * 68}")
    for method, count in methods.most_common():
        print(f"  {method:20} {count:4}  {count / len(files):6.0%}")
    print(f"\n  needing human review: {len(needs_review)}")
    for line in needs_review[:15]:
        print(f"    {line}")

    tables = stats["tables_parsed"] + stats["tables_needing_mapping"]
    print(f"\n{'=' * 68}\nTABLE EXTRACTION — {tables} tables found\n{'=' * 68}")
    print(f"  parsed automatically   {stats['tables_parsed']:4}")
    print(f"  header unrecognised    {stats['tables_needing_mapping']:4}   <- the only LLM calls")
    for line in unmapped[:10]:
        print(f"      {line}")

    total = stats["rows"] or 1
    print(f"\n{'=' * 68}\nSTRUCTURED ROWS — {stats['rows']}\n{'=' * 68}")
    for key in ("price", "quantity", "ean", "brand", "capacity", "category", "from_section"):
        print(f"  {key:14} {stats[key]:6}  {stats[key] / total:6.0%}")

    prose = stats["prose_lines"] or 1
    print(f"\nPROSE EMAILS — {stats['prose_lines']} lines "
          f"(brand {stats['prose_brand'] / prose:.0%}, price {stats['prose_price'] / prose:.0%}, "
          f"{stats['expanded']} expanded from colour lists)")

    print("\n  rows with no brand:")
    for line in unbranded[:8]:
        print(f"    {line}")

    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    raise SystemExit(main(target))
