"""One way in to a model, so failure is handled once — and so the provider is a setting.

Two providers are supported: Anthropic, and anything speaking the OpenAI chat-completions
shape, which is nearly every other option including the free tiers. Which one is used is
decided by which key is filled in, so moving between them is editing `.env` and nothing
else. Nothing above this file knows or cares: the closed-set check in `side.py` and the
corroboration in `columns.py` protect against a weak model exactly as they protect
against a strong one.

The OpenAI-compatible path is written against `urllib` from the standard library rather
than an SDK. That is deliberate. Two Render deploys have already been lost to pip
resolving one package against another, and a POST built out of the standard library
cannot fail to install.

Everything here is written so a caller can ignore the possibility of a model entirely:
`ask` returns an `Answer` or it returns `None`, and `None` covers no key, no package, a
revoked key, a rate limit, an outage, a timeout and a malformed reply alike.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from app.config import get_settings

log = logging.getLogger(__name__)

Role = Literal["classify", "extract"]

# US dollars per million tokens, input then output. A local copy of published pricing,
# used for nothing but filling usage_events.cost_usd. The token counts beside it are
# measured; this is arithmetic over a number that changes without telling us. Being wrong
# here produces a wrong cost report and never a wrong board — and an unrecognised model,
# which includes every free tier, records its tokens at zero rather than inventing a rate.
_RATES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (15.00, 75.00),
}

# Sent on every request, and not optional. urllib identifies itself as
# 'Python-urllib/3.11', which sits on the default bot list of every CDN — Groq is behind
# Cloudflare and refuses it with a 403 and error code 1010, before the API sees the
# request or the key. The failure looks exactly like a bad key and is not one.
_USER_AGENT = "import-export-backend/0.1"

HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

# Floor for the completion budget on the OpenAI-compatible path. See _ask_openai.
_REASONING_HEADROOM = 512


@dataclass(frozen=True)
class Answer:
    """What the model said, and what it cost to ask."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0

    @property
    def cost_usd(self) -> float:
        rate = _RATES.get(self.model)
        if rate is None:
            return 0.0
        input_cost = self.input_tokens / 1_000_000 * rate[0]
        output_cost = self.output_tokens / 1_000_000 * rate[1]
        return round(input_cost + output_cost, 6)


@dataclass(frozen=True)
class Provider:
    kind: Literal["anthropic", "openai"]
    api_key: str
    base_url: str
    classify_model: str
    extract_model: str

    def model_for(self, role: Role) -> str:
        return self.classify_model if role == "classify" else self.extract_model


def provider() -> Provider | None:
    """Which model service to use, or `None` if none is configured.

    Anthropic wins when its key is present, so tomorrow's switch is pasting one line —
    the temporary provider can be left in place and simply stops being consulted.
    """
    settings = get_settings()

    if settings.anthropic_api_key:
        return Provider(
            kind="anthropic",
            api_key=settings.anthropic_api_key,
            base_url="",
            classify_model=settings.classify_model,
            extract_model=settings.extract_model,
        )

    if settings.llm_api_key and settings.llm_base_url:
        return Provider(
            kind="openai",
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url.rstrip("/"),
            # Falling back to the same model for both is right for a free tier, where
            # there is usually only one worth using anyway.
            classify_model=settings.llm_classify_model or settings.llm_extract_model,
            extract_model=settings.llm_extract_model or settings.llm_classify_model,
        )

    return None


def available() -> bool:
    """Whether a model can be reached at all. Callers use this to skip building a prompt."""
    chosen = provider()
    return chosen is not None and bool(chosen.model_for("classify"))


def ask(
    system: str,
    user: str,
    *,
    role: Role = "classify",
    max_tokens: int = 64,
    prefill: str | None = None,
    timeout: float = 20.0,
) -> Answer | None:
    """Ask the model one question. `None` means the answer must come from elsewhere."""
    chosen = provider()
    if chosen is None:
        return None

    model = chosen.model_for(role)
    if not model:
        log.warning("no model name configured for %s; set LLM_%s_MODEL", role, role.upper())
        return None

    started = time.monotonic()
    try:
        if chosen.kind == "anthropic":
            text, tokens = _ask_anthropic(chosen, model, system, user, max_tokens, prefill, timeout)
        else:
            text, tokens = _ask_openai(chosen, model, system, user, max_tokens, timeout)
    except Exception as error:
        # Deliberately broad. An import failure, a bad key, a 429, a socket timeout and a
        # provider returning HTML all mean the same thing to every caller in this package:
        # decide without it.
        log.warning("model call failed (%s via %s): %s", model, chosen.kind, error)
        return None

    return Answer(
        text=text.strip(),
        model=model,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        duration_ms=int((time.monotonic() - started) * 1000),
    )


# ---------------------------------------------------------------- anthropic


@lru_cache(maxsize=4)
def _client(api_key: str, timeout: float):
    """Reuse the client across calls; it holds a connection pool.

    Keyed by the key so a settings change in a test does not hand back a client
    authenticated as somebody else.
    """
    from anthropic import Anthropic

    # One retry, not the default of two. This sits inside an email pipeline that n8n is
    # waiting on, and a third attempt at a model that is down costs more in latency than
    # the answer is worth — the deterministic result is already in hand.
    return Anthropic(api_key=api_key, timeout=timeout, max_retries=1)


def _ask_anthropic(
    chosen: Provider,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    prefill: str | None,
    timeout: float,
) -> tuple[str, tuple[int, int]]:
    messages: list[dict] = [{"role": "user", "content": user}]
    if prefill:
        # Putting the opening brace in the assistant's mouth. Without it a model tends to
        # introduce its JSON in a sentence first, which costs output tokens and leaves the
        # caller parsing around prose.
        messages.append({"role": "assistant", "content": prefill})

    response = _client(chosen.api_key, timeout).messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=system,
        messages=messages,
    )

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    if prefill:
        text = prefill + text

    usage = getattr(response, "usage", None)
    return text, (
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
    )


# ---------------------------------------------------------------- openai-compatible


def _ask_openai(
    chosen: Provider,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: float,
) -> tuple[str, tuple[int, int]]:
    """A chat-completions POST, which is what Groq, Gemini, OpenRouter and the rest speak.

    No prefill here: an assistant turn to be continued is an Anthropic feature and most
    compatible endpoints either ignore it or reject it. `columns._validate` already digs
    the JSON object out of surrounding prose with a regex, which is exactly the case this
    leaves it to handle.
    """
    # Reasoning models spend completion tokens thinking before they answer, and the
    # budget covers both. Eight tokens is the right size for a model that replies with
    # one word directly, and buys nothing but a truncated reply and an empty `content`
    # from a model that reasons first. Room costs nothing when it goes unused: billing
    # and rate limits count tokens produced, not tokens allowed.
    max_tokens = max(max_tokens, _REASONING_HEADROOM)

    body = json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()

    request = urllib.request.Request(  # noqa: S310 — the URL is our own configuration
        f"{chosen.base_url}/chat/completions",
        data=body,
        headers={
            **HEADERS,
            "Authorization": f"Bearer {chosen.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        # The body carries the reason — a wrong model name, an exhausted free quota —
        # and without it the log says only '400 Bad Request', which explains nothing.
        detail = error.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{error.code} {error.reason}: {detail}") from None

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"no choices in reply: {str(payload)[:200]}")

    message = choices[0].get("message") or {}
    text = message.get("content") or ""

    # A reasoning model that ran out of budget mid-thought returns its thinking and no
    # answer. Worth naming, because an empty string would otherwise fail the closed-set
    # check and be logged as the model ignoring its instructions.
    if not text and message.get("reasoning"):
        raise RuntimeError(f"spent the whole {max_tokens}-token budget reasoning, no answer")

    usage = payload.get("usage") or {}
    return text, (
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
    )
