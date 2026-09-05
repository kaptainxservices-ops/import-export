"""Prove the model path works, before pointing 45 emails at it.

Three questions, in the order you hit them:

    which provider am I actually configured against?
    what are its models called today?
    does a real call come back with something usable?

Run it with no model name set and it answers the second question and stops. Set the name
it suggests, run it again, and it answers the third — a real classification and a real
column mapping, on the two shapes of input the system will meet.

    python scripts/check_llm.py

Nothing here writes to the database or costs more than two small calls.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brain.llm import columns as llm_columns  # noqa: E402
from app.brain.llm.client import HEADERS, provider  # noqa: E402
from app.brain.llm.side import classify_side_llm  # noqa: E402
from app.config import get_settings  # noqa: E402

# Names that are not chat models. Providers list speech, moderation and embedding models
# beside the useful ones, and 'orpheus' is a text-to-speech family that reads like a
# perfectly good LLM if you are matching on nothing but string length.
_NOT_CHAT = (
    "whisper", "tts", "guard", "embed", "moderation", "safety", "rerank",
    "orpheus", "canopylabs", "playai", "speech", "audio", "asr", "voice", "vision",
)

# Families known to follow a short instruction and emit clean JSON, best first. Used only
# to suggest a default — anything on the list will work, and the corroboration check in
# columns.py is what actually decides whether an answer is usable.
_PREFERRED = ("gpt-oss", "qwen", "llama", "mixtral", "gemma", "compound")

# A real table with a header the rules cannot read: Polish, abbreviated. If the model
# gets this right it will get the eight in the samples right.
_HEADER = ["Poz.", "Towar", "Szt.", "Kwota"]
_ROWS = [
    ["1", "iPhone 13 128GB Black", "12", "410,00"],
    ["2", "iPhone 13 256GB Blue", "6", "455,00"],
    ["3", "Samsung S23 256GB Green", "20", "389,50"],
    ["4", "Samsung A54 128GB Black", "44", "188,00"],
    ["5", "Xiaomi Redmi Note 12 128GB", "31", "121,00"],
]


def list_models(base_url: str, api_key: str) -> list[str]:
    request = urllib.request.Request(  # noqa: S310 — the URL is our own configuration
        f"{base_url.rstrip('/')}/models",
        headers={**HEADERS, "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        payload = json.loads(response.read().decode())

    return sorted(str(item.get("id", "")) for item in payload.get("data", []) if item.get("id"))


def _size(name: str) -> float:
    """Parameter count as written in the name — '120b' is 120. Zero when unstated."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*b\b", name.lower())
    return float(match.group(1)) if match else 0.0


def _family(name: str) -> int:
    """How well a family follows a short instruction. Higher is better, 0 is unknown."""
    lowered = name.lower()
    for score, marker in enumerate(reversed(_PREFERRED), start=1):
        if marker in lowered:
            return score
    return 0


def _suggest(chat: list[str]) -> tuple[str, str]:
    """A small model for the three-way question, a large one for reading a header.

    The same split as the Anthropic defaults — Haiku classifies, Sonnet extracts. Easier
    on a free tier's rate limit, and the small one is being asked to choose one word out
    of three, which is not work that rewards size.
    """
    best = max(_family(name) for name in chat)
    family = [name for name in chat if _family(name) == best] or chat
    by_size = sorted(family, key=_size)
    return by_size[0], by_size[-1]


def _explain(code: int, body: str) -> str:
    """Say what the status actually means here, rather than guessing at one of them.

    These three get confused constantly because all of them look like 'the key is
    broken', and only one of them is.
    """
    if "1010" in body or "cloudflare" in body.lower():
        return (
            "That is Cloudflare, not the API — it blocked the request on its user-agent\n"
            "  before the key was ever checked. Fixed in app/brain/llm/client.py; if you\n"
            "  are still seeing it, the code is stale."
        )
    if code == 401:
        return "The key is wrong, expired, or from a different provider."
    if code == 403:
        return "The key is valid but not allowed to do this — check the project it belongs to."
    if code == 404:
        return (
            "Wrong base URL. It must end in the version path, e.g.\n"
            "  https://api.groq.com/openai/v1  — not the bare hostname."
        )
    if code == 429:
        return "Rate limited or the free quota is spent. Try again shortly."
    return "Unexpected. The body above is the provider's own explanation."


def main() -> int:
    settings = get_settings()
    chosen = provider()

    if chosen is None:
        print("No model provider configured.\n")
        print("Put these two in .env, then run this again:")
        print("  LLM_BASE_URL=https://api.groq.com/openai/v1")
        print("  LLM_API_KEY=gsk_...")
        return 1

    print(f"provider   {chosen.kind}")
    if chosen.base_url:
        print(f"base url   {chosen.base_url}")
    print(f"key        ...{chosen.api_key[-4:]}  ({len(chosen.api_key)} chars)")

    # ---------------------------------------------------------------- models

    if chosen.kind == "openai" and not chosen.classify_model:
        print("\nNo model name set. Asking the provider what it has.\n")
        try:
            names = list_models(chosen.base_url, chosen.api_key)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            print(f"  could not list models: {error.code} {error.reason}")
            print(f"  {detail.strip()}")
            print(f"\n  {_explain(error.code, detail)}")
            return 1
        except Exception as error:
            print(f"  could not reach {chosen.base_url}: {error}")
            return 1

        chat = [n for n in names if not any(bad in n.lower() for bad in _NOT_CHAT)]

        print("  chat models available:")
        for name in chat:
            print(f"    {name}")
        if not chat:
            print("    (none recognised — try one from the full list below)")
            for name in names:
                print(f"    {name}")

        if chat:
            small, large = _suggest(chat)
            print("\n  Put these two in .env — the small one answers a question with three")
            print("  possible answers, the large one reads an unfamiliar table header:")
            print(f"    LLM_CLASSIFY_MODEL={small}")
            print(f"    LLM_EXTRACT_MODEL={large}")
        return 1

    print(f"classify   {chosen.model_for('classify')}")
    print(f"extract    {chosen.model_for('extract')}")

    # ---------------------------------------------------------------- real calls

    print("\n--- classification " + "-" * 53)
    print("  a forwarded WhatsApp offer, with no WTS or WTB anywhere in it")

    verdict = classify_side_llm(
        "Fwd: message",
        "iPhone 13 128GB Black 410\niPhone 14 256GB Blue 505\nall available now",
    )
    if verdict is None:
        print("  FAILED — no answer came back. The log line above says why.")
        return 1

    print(f"  answered   {verdict.side!r}  (expected 'sell')")
    print(f"  tokens     {verdict.answer.input_tokens} in, {verdict.answer.output_tokens} out")
    print(f"  took       {verdict.answer.duration_ms} ms")
    if verdict.side != "sell":
        print("  ^ not what a person would say. Try a larger model.")

    print("\n--- column mapping " + "-" * 53)
    print(f"  header: {_HEADER}")

    llm_columns.clear_cache()
    mapping = llm_columns.map_columns_llm(_HEADER, _ROWS)

    if mapping is None:
        print("  refused. Either the model declined, or it named a column that did not")
        print("  hold up against the rows — which is the check doing its job.")
        print("  Not fatal: those tables go to the review queue, as they do today.")
    else:
        print(f"  mapped:  {mapping.columns}")
        print("  expected: description 1, quantity 2, price 3")

    spend = settings.anthropic_api_key and "check the Anthropic console" or "free tier"
    print(f"\ndone — model path is live ({spend}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
