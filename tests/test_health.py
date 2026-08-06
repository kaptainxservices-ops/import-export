"""Health endpoint. Render's deploy check and UptimeRobot both depend on this."""

from app import __version__


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["env"] == "test"


def test_health_needs_no_token(client):
    """Deliberate: neither Render nor UptimeRobot can send a secret header."""
    assert client.get("/health").status_code == 200
