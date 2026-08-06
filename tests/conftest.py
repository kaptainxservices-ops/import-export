"""Shared fixtures.

Settings are cached, so every test that changes the environment must clear the cache
or it silently reads a stale value from an earlier test.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

TEST_TOKEN = "test-ingest-token"  # noqa: S105 — not a real secret


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch):
    """Give every test a known environment, independent of any local .env."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("INGEST_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_email() -> dict:
    """A minimal but realistic payload in the shape n8n will send."""
    return {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "message_id": "<abc123@supplier.example>",
        "from_email": "sales@supplier.example",
        "from_name": "Al Manar Trading",
        "to": ["desk@broker.example"],
        "subject": "Stock list 06 Aug",
        "received_at": "2026-08-06T09:30:00Z",
        "body_text": "40x iPhone 15 Pro Max 256GB Blue A grade LL/A @ 905 USD",
        "body_raw": "40x iPhone 15 Pro Max 256GB Blue A grade LL/A @ 905 USD\n\n> older quote",
        "attachments": [],
    }
