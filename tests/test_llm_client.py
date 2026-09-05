"""Which model service gets used, and how it is spoken to.

The point of this seam is that everything above it — the three-word answer, the column
corroboration — is unchanged by the answer to 'whose model?'. These tests hold the seam
in place, and one of them holds the switch back to Anthropic to a single line of .env.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from app.brain.llm import client
from app.brain.llm.client import Answer, available, provider
from app.config import get_settings


def _configure(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


GROQ = dict(
    LLM_BASE_URL="https://api.groq.example/openai/v1",
    LLM_API_KEY="gsk_test",
    LLM_CLASSIFY_MODEL="llama-3.3-70b",
)


# ---------------------------------------------------------------- choosing


def test_no_key_no_provider():
    assert provider() is None
    assert available() is False


def test_an_openai_compatible_provider_is_used_when_configured(monkeypatch):
    _configure(monkeypatch, **GROQ)

    chosen = provider()
    assert chosen is not None
    assert chosen.kind == "openai"
    assert available() is True


def test_one_model_name_serves_both_roles(monkeypatch):
    """Free tiers usually have one model worth using. Naming it once is enough."""
    _configure(monkeypatch, **GROQ)

    chosen = provider()
    assert chosen is not None
    assert chosen.model_for("classify") == chosen.model_for("extract") == "llama-3.3-70b"


def test_anthropic_takes_over_the_moment_its_key_appears(monkeypatch):
    """The switch is pasting one line. The temporary provider can stay in the file and
    simply stops being consulted — nothing else to remember to undo."""
    _configure(monkeypatch, **GROQ, ANTHROPIC_API_KEY="sk-ant-test")

    chosen = provider()
    assert chosen is not None
    assert chosen.kind == "anthropic"
    assert chosen.model_for("classify") == "claude-haiku-4-5-20251001"


def test_a_base_url_without_a_key_is_not_a_provider(monkeypatch):
    _configure(monkeypatch, LLM_BASE_URL="https://api.groq.example/openai/v1")
    assert provider() is None


# ---------------------------------------------------------------- speaking


class _Reply:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _intercept(monkeypatch, payload: dict):
    """Catch the outbound request instead of making it."""
    sent: dict = {}

    def fake_urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["headers"] = {k.lower(): v for k, v in request.headers.items()}
        sent["body"] = json.loads(request.data.decode())
        sent["timeout"] = timeout
        return _Reply(payload)

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_the_openai_path_posts_chat_completions(monkeypatch):
    _configure(monkeypatch, **GROQ)
    sent = _intercept(
        monkeypatch,
        {
            "choices": [{"message": {"content": "sell"}}],
            "usage": {"prompt_tokens": 812, "completion_tokens": 1},
        },
    )

    answer = client.ask("you classify emails", "Subject: Fwd: message", role="classify")

    assert answer is not None
    assert answer.text == "sell"
    assert answer.input_tokens == 812
    assert answer.output_tokens == 1
    assert sent["url"] == "https://api.groq.example/openai/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer gsk_test"
    assert sent["body"]["model"] == "llama-3.3-70b"
    assert sent["body"]["temperature"] == 0
    assert [m["role"] for m in sent["body"]["messages"]] == ["system", "user"]


def test_a_user_agent_is_always_sent(monkeypatch):
    """urllib announces itself as 'Python-urllib/3.11', which is on the default bot list
    of every CDN. Groq sits behind Cloudflare and returns 403 error 1010 — before the API
    sees the request or the key. It looks exactly like a bad key and is not one."""
    _configure(monkeypatch, **GROQ)
    sent = _intercept(monkeypatch, {"choices": [{"message": {"content": "sell"}}]})

    client.ask("s", "u")

    agent = sent["headers"]["user-agent"]
    assert agent and "urllib" not in agent.lower()


def test_no_prefill_is_sent_to_a_compatible_endpoint(monkeypatch):
    """An assistant turn to be continued is an Anthropic feature; most compatible
    endpoints ignore it or reject it. The JSON is dug out of prose instead."""
    _configure(monkeypatch, **GROQ)
    sent = _intercept(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})

    client.ask("map the columns", "header: ...", role="extract", prefill="{")

    assert all(m["role"] != "assistant" for m in sent["body"]["messages"])


def test_a_trailing_slash_on_the_base_url_does_not_double_up(monkeypatch):
    _configure(monkeypatch, **{**GROQ, "LLM_BASE_URL": "https://api.groq.example/openai/v1/"})
    sent = _intercept(monkeypatch, {"choices": [{"message": {"content": "buy"}}]})

    client.ask("s", "u")

    assert sent["url"] == "https://api.groq.example/openai/v1/chat/completions"


def test_an_http_error_is_an_absent_answer_not_an_exception(monkeypatch):
    """An exhausted free quota arrives as a 429 in the middle of parsing an email. The
    email still has to be filed."""
    _configure(monkeypatch, **GROQ)

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests", {}, None  # type: ignore[arg-type]
        )

    monkeypatch.setattr(client.urllib.request, "urlopen", refuse)

    assert client.ask("s", "u") is None


def test_a_reply_with_no_choices_is_an_absent_answer(monkeypatch):
    _configure(monkeypatch, **GROQ)
    _intercept(monkeypatch, {"error": {"message": "model_decommissioned"}})

    assert client.ask("s", "u") is None


def test_a_missing_model_name_is_refused_before_the_call(monkeypatch):
    _configure(monkeypatch, LLM_BASE_URL="https://api.groq.example/openai/v1", LLM_API_KEY="k")

    def explode(*args, **kwargs):
        raise AssertionError("a request was made with no model name")

    monkeypatch.setattr(client.urllib.request, "urlopen", explode)

    assert client.ask("s", "u") is None
    assert available() is False


# ---------------------------------------------------------------- accounting


def test_an_unpriced_model_records_its_tokens_at_zero(monkeypatch):
    """Free tiers are not in the rate table, which is the honest answer: the tokens are
    measured, and nothing is invented to put beside them."""
    answer = Answer(text="sell", model="llama-3.3-70b", input_tokens=900, output_tokens=1)
    assert answer.cost_usd == 0.0


def test_a_priced_model_is_costed():
    answer = Answer(
        text="sell", model="claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=2
    )
    assert answer.cost_usd == pytest.approx(0.00101)
