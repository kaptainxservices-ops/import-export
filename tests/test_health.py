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


def test_readiness_reports_the_database(client):
    from app.db.memory import InMemoryRepository
    from app.dependencies import set_repository

    set_repository(InMemoryRepository([]))
    try:
        body = client.get("/health/ready").json()
        assert body["status"] == "ok"
        assert body["database"] == "reachable"
    finally:
        set_repository(None)


def test_readiness_degrades_rather_than_erroring(client):
    """A database outage must not make the readiness check itself fail — Render would
    restart a service whose health endpoint errors, and restarting fixes nothing when
    the problem is Supabase."""
    class Broken:
        def get_tenant(self, tenant_id):
            raise RuntimeError("connection refused")

    from app.dependencies import set_repository

    set_repository(Broken())
    try:
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
    finally:
        set_repository(None)
