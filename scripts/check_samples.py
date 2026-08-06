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

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brain.normalise import (  # noqa: E402
    expand_variants,
    parse_price,
    parse_product,
    parse_quantity,
)
from app.brain.sender import resolve_sender  # noqa: E402

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


def table_rows(html: str) -> list[str]:
    """Rows from any HTML table big enough to be a price list."""
    if not html:
        return []
    rows: list[str] = []
    for table in BeautifulSoup(html, "lxml").find_all("table"):
        trs = table.find_all("tr")
        if len(trs) < 4:
            continue
        for tr in trs:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                rows.append(" | ".join(cells))
    return rows


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
    stats = Counter()
    unbranded: list[str] = []

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

        candidates = table_rows(html)[:400] or prose_lines(text)[:400]
        for line in candidates:
            for variant in expand_variants(line):
                spec = parse_product(variant.text)
                stats["lines"] += 1
                if spec.brand:
                    stats["brand"] += 1
                else:
                    if len(unbranded) < 15 and len(variant.text) > 20:
                        unbranded.append(variant.text[:90])
                if spec.category:
                    stats["category"] += 1
                if spec.ean:
                    stats["ean"] += 1
                if spec.capacity_gb:
                    stats["capacity"] += 1
                if spec.colour:
                    stats["colour"] += 1
                if variant.expanded_from_colours:
                    stats["expanded"] += 1
                if PRICE_LIKE.search(variant.text) and parse_price(variant.text) is not None:
                    stats["price"] += 1
                if parse_quantity(variant.text):
                    stats["quantity"] += 1

    print(f"\n{'=' * 68}\nSENDER RESOLUTION — {len(files)} emails\n{'=' * 68}")
    for method, count in methods.most_common():
        print(f"  {method:20} {count:4}  {count / len(files):6.0%}")
    print(f"\n  needing human review: {len(needs_review)}")
    for line in needs_review[:15]:
        print(f"    {line}")

    total = stats["lines"] or 1
    print(f"\n{'=' * 68}\nEXTRACTION COVERAGE — {total} candidate lines\n{'=' * 68}")
    for key in ("brand", "category", "ean", "capacity", "colour", "price", "quantity", "expanded"):
        print(f"  {key:12} {stats[key]:6}  {stats[key] / total:6.0%}")

    print("\n  sample lines with no brand detected:")
    for line in unbranded[:10]:
        print(f"    {line}")

    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    raise SystemExit(main(target))
