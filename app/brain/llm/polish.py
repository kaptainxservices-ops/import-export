"""Rewrite a quote email's wording without touching a single figure.

The email is built from the database: every price, quantity and spec in it is a value
the system computed. The model is allowed to improve how it reads and nothing else, so
its answer is checked rather than trusted. Every number in the draft must appear in the
rewrite exactly as many times as before, and no new number may appear. A rewrite that
fails the check is thrown away and the client keeps the draft he had.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.brain.llm.client import ask, available

_SYSTEM = """You polish short business emails in the wholesale electronics trade. A \
broker is quoting stock to a buyer.

Improve the wording so it reads naturally, politely and professionally. Keep it short.

Strict rules:
- Do NOT change, add, remove, round or reformat ANY number: prices, quantities, \
percentages, storage sizes, model numbers. Copy each one exactly as written.
- Keep every product line, in the same order, with the same details.
- Keep currency symbols and codes exactly as written.
- Do not invent terms, dates, delivery times, payment terms or promises.
- Do not mention suppliers.
- Reply with the email text only. No preamble, no explanation, no markdown."""

# A figure as it is written: '1,079.50', '6/128', '17', '3.5'. Separators are part of
# the figure, so '1,079.50' rewritten as '1079.5' counts as a change.
_FIGURE = re.compile(r"\d+(?:[.,/]\d+)*")


def figures(text: str) -> Counter[str]:
    return Counter(_FIGURE.findall(text or ""))


def same_figures(before: str, after: str) -> bool:
    return figures(before) == figures(after)


@dataclass(frozen=True)
class Polished:
    text: str | None  # None: no rewrite to offer
    reason: str | None = None


def polish(draft: str) -> Polished:
    if not available():
        return Polished(None, "No AI model is configured on the server.")
    draft = (draft or "").strip()
    if not draft:
        return Polished(None, "The email is empty.")

    answer = ask(_SYSTEM, draft[:8000], role="extract", max_tokens=2000, timeout=45.0)
    if answer is None or not answer.text.strip():
        return Polished(None, "The AI model did not answer. Try again in a moment.")

    text = answer.text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].strip()
    if not same_figures(draft, text):
        return Polished(
            None,
            "The rewrite changed a price, quantity or spec, so it was thrown away. "
            "Your draft is unchanged.",
        )
    return Polished(text)
