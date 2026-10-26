"""Settling, once each, the model names the rules cannot.

canonical.py writes every model one way, and for nearly every line that is the end of
it: 'SAM S23' and 'Samsung Galaxy S23 5G' are both samsung:s23 without anyone being
asked. What is left is a line carrying a word the rules do not know --

    oneplus:nordce6smooth        against   oneplus:nordce6
    jbl:tune720btdeluxe         against   jbl:tune720bt

-- where the extra word might be a colour, a description, or a different product. The
board shows such a pair as a near miss, to be confirmed by hand, until it is settled.

This settles it. Three rules, in order of importance.

**Code decides what may be asked.** A pair reaches the model only when it shares its
model number and differs in nothing but descriptive words: no number, no variant word
(Pro, Max, Ultra, FE, Plus, Lite, Edge...), no accessory word (case, keyboard, pencil).
An S23 and an S23 Ultra are never put to the model, so no answer it gives can merge
them. See `differs_only_in_description` in canonical.py.

**Every answer is kept, including 'no'.** A stored difference saves paying for the same
question on every list; a stored match turns the next one into a lookup. Suppliers
resend the same wording every morning, so after the first few days this almost never
calls out at all.

**A person outranks the model.** A row written by hand is never overwritten.

The provider is whatever app/brain/llm/client.py is configured with -- the
OpenAI-compatible LLM_* settings today, Anthropic when its key is set -- and with none
configured this does nothing and the board matches on the rules alone.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.brain.llm.client import ask, available
from app.brain.llm.usage import UsageLog
from app.brain.normalise.canonical import (
    CanonicalModel,
    canonical_model,
    differs_only_in_description,
)

log = logging.getLogger(__name__)

# Per email. A first run against a full board asks the most; every run after asks
# almost nothing. The cap keeps a pathological list from becoming a pathological bill.
MAX_QUESTIONS = 60
BATCH = 15
MAX_CANDIDATES = 4


@dataclass(frozen=True)
class Question:
    key: str
    wording: str
    # (candidate key, an example of how the board writes it)
    candidates: tuple[tuple[str, str], ...]


def _brands_agree(a: CanonicalModel, b: CanonicalModel) -> bool:
    return not a.brand or not b.brand or a.brand == b.brand


def _categories_agree(a: CanonicalModel, b: CanonicalModel) -> bool:
    return not a.category or not b.category or a.category == b.category


def pending_questions(
    lines: list[tuple[str, str | None, str | None]],
    catalogue: list[tuple[str, str | None, str | None]],
    settled: dict[str, str | None],
) -> list[Question]:
    """The pairs worth asking about: new wordings with a close, unsettled neighbour."""
    known: dict[str, tuple[CanonicalModel, str]] = {}
    for description, brand, colour in catalogue:
        model = canonical_model(description, brand=brand, colour=colour)
        if model.key and model.key not in known:
            known[model.key] = (model, description)

    questions: dict[str, Question] = {}
    for description, brand, colour in lines:
        model = canonical_model(description, brand=brand, colour=colour)
        if not model.key or model.key in settled or model.key in questions:
            continue

        candidates = [
            (key, example)
            for key, (other, example) in known.items()
            if key != model.key
            and key not in settled
            and _brands_agree(model, other)
            and _categories_agree(model, other)
            and differs_only_in_description(model.tokens, other.tokens)
        ]
        if candidates:
            candidates.sort(key=lambda pair: len(pair[0]))
            questions[model.key] = Question(
                key=model.key, wording=description,
                candidates=tuple(candidates[:MAX_CANDIDATES]),
            )
        if len(questions) >= MAX_QUESTIONS:
            break

    return list(questions.values())


_SYSTEM = """You match product names in the wholesale consumer-electronics trade.

For each numbered item you get one NEW product line and up to four CANDIDATE models
already on a trading board. Decide whether the NEW line is exactly the same product
model as one candidate.

Same model means: same brand, same product line, same model number and the same
variant. Words that only describe colour, finish, packaging, condition, or restate the
product type ("wireless", "headphones", "smart glasses") do NOT make a different model.
A word that names a different version, size, generation, edition or a different kind of
product DOES.

Reply with JSON only, no prose:
{"answers": [{"id": 1, "match": 2}, {"id": 2, "match": 0}]}

"match" is the candidate number, or 0 when no candidate is the same model or you are
not certain. A wrong match makes a trader quote the wrong product; 0 costs one click."""


def _prompt(batch: list[Question]) -> str:
    parts = []
    for number, question in enumerate(batch, start=1):
        lines = [f"{number}. NEW: {question.wording!r}"]
        for index, (_, example) in enumerate(question.candidates, start=1):
            lines.append(f"   candidate {index}: {example!r}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _parse(text: str, batch: list[Question]) -> dict[int, int]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    out: dict[int, int] = {}
    for item in data.get("answers") or []:
        try:
            number, choice = int(item.get("id")), int(item.get("match"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= number <= len(batch) and 0 <= choice <= len(batch[number - 1].candidates):
            out[number] = choice
    return out


def ask_about(questions: list[Question], usage: UsageLog | None = None) -> list[dict]:
    """Put the questions to the configured model. Returns rows for the model_equivalences table."""
    if not questions or not available():
        return []

    rows: list[dict] = []
    for start in range(0, len(questions), BATCH):
        batch = questions[start : start + BATCH]
        answer = ask(_SYSTEM, _prompt(batch), role="extract", max_tokens=40 * len(batch) + 60)
        if usage is not None:
            usage.add("resolve_models", answer)
        if answer is None:
            continue

        choices = _parse(answer.text, batch)
        for number, question in enumerate(batch, start=1):
            if number not in choices:
                # No answer for this one: ask again next time rather than store a guess.
                continue
            choice = choices[number]
            if choice == 0:
                rows.append({
                    "from_key": question.key, "to_key": None, "source": "model",
                    "model": answer.model,
                    "note": f"different from {question.candidates[0][0]}",
                })
                continue
            target = question.candidates[choice - 1][0]
            # Always map the longer wording onto the shorter one, so that a third
            # wording later joins the same model instead of starting a chain.
            if len(question.key) >= len(target):
                source_key, target_key = question.key, target
            else:
                source_key, target_key = target, question.key
            rows.append({"from_key": source_key, "to_key": target_key, "source": "model",
                         "model": answer.model, "note": question.wording[:200]})
    return rows


def resolve_new_models(
    repo, tenant_id: str, lines: list[tuple[str, str | None, str | None]], usage: UsageLog
) -> int:
    """After a list is applied: settle its new wordings. Never raises; returns rows stored."""
    if not lines or not available():
        return 0
    try:
        settled = repo.load_model_aliases(tenant_id)
        questions = pending_questions(lines, repo.model_catalogue(tenant_id), settled)
        rows = ask_about(questions, usage)
        return repo.save_model_aliases(tenant_id, rows) if rows else 0
    except Exception as error:  # noqa: BLE001 -- a convenience, never an email failure
        log.warning("model resolution skipped: %s", error)
        return 0


def effective_aliases(raw: dict[str, str | None]) -> dict[str, str]:
    """from -> final target, following at most a few hops; 'different' rows dropped."""
    out: dict[str, str] = {}
    for source, target in raw.items():
        seen = {source}
        while target and target in raw and raw[target] and target not in seen:
            seen.add(target)
            target = raw[target]
        if target:
            out[source] = target
    return out
