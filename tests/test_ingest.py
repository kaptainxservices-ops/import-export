"""/ingest — the only write path into the system, so its guard is tested first.

These are security tests, not feature tests. If they ever go red, anyone on the
internet can post fake offers onto the client's board.
"""

import pytest

from app.config import get_settings
from tests.conftest import TEST_TOKEN


def test_rejects_missing_token(client, sample_email):
    response = client.post("/ingest", json=sample_email)
    assert response.status_code == 401


def test_rejects_wrong_token(client, sample_email):
    response = client.post(
        "/ingest", json=sample_email, headers={"X-Ingest-Token": "not-the-token"}
    )
    assert response.status_code == 401


def test_rejects_token_that_is_a_prefix(client, sample_email):
    """Guards against a comparison that only checks the start of the string."""
    response = client.post(
        "/ingest", json=sample_email, headers={"X-Ingest-Token": TEST_TOKEN[:5]}
    )
    assert response.status_code == 401


def test_accepts_valid_token(client, sample_email):
    response = client.post(
        "/ingest", json=sample_email, headers={"X-Ingest-Token": TEST_TOKEN}
    )

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["message_id"] == sample_email["message_id"]


def test_refuses_when_server_has_no_token_configured(
    client, sample_email, monkeypatch: pytest.MonkeyPatch
):
    """A misconfigured server must refuse, never wave everything through."""
    monkeypatch.setenv("INGEST_TOKEN", "")
    get_settings.cache_clear()

    response = client.post(
        "/ingest", json=sample_email, headers={"X-Ingest-Token": TEST_TOKEN}
    )
    assert response.status_code == 503


def test_rejects_malformed_payload(client):
    """A valid token is not a licence to send rubbish."""
    response = client.post(
        "/ingest", json={"subject": "no required fields"}, headers={"X-Ingest-Token": TEST_TOKEN}
    )
    assert response.status_code == 422
