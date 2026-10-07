import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.config import Settings
from backend.demo import DEMO_USERNAME, seed_demo
from backend.security.auth import Auth, hashed


def test_demo_allows_draft_edits_but_cannot_queue_network_work(db, tmp_path):
    demo_password = secrets.token_urlsafe(24)
    with db.tx(write=True) as tx:
        tx.put(
            "system",
            {
                "token_hash": hashed("demo-test-bootstrap"),
                "expires": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            },
            "bootstrap",
        )
    Auth(db).setup(DEMO_USERNAME, demo_password, "demo-test-bootstrap")
    seed_demo(db)
    settings = Settings(state=tmp_path, origin="http://testserver", demo_mode=True)
    with TestClient(create_app(settings, db)) as client:
        client.headers["Origin"] = settings.origin
        assert client.get("/api/v1/setup/status").json()["demo_mode"] is True
        assert (
            client.post(
                "/api/v1/session",
                json={
                    "username": DEMO_USERNAME,
                    "password": demo_password,
                },
            ).status_code
            == 200
        )
        client.headers["X-CSRF-Token"] = client.cookies["campus_csrf"]
        for route in [
            "/discovery-runs",
            "/access-profiles",
            "/host-identities",
            "/connectors/demo-inventory/validate",
            "/connectors/demo-inventory/inventory-options",
            "/connectors/demo-inventory/inventory-objects",
            "/baseline-drafts/example/approve",
            "/repair-plans/example/approve-and-run",
            "/check-policies",
            "/configuration-reviews",
            "/setup/administrator",
            "/account/password",
        ]:
            response = client.post("/api/v1" + route, json={})
            assert response.status_code == 403, (route, response.status_code)
            assert "demo supports draft editing" in response.json()["message"]
        client.headers["Idempotency-Key"] = str(uuid4())
        response = client.post(
            "/api/v1/devices",
            json={
                "label": "Draft added in demo",
                "role": "unknown",
                "addresses": ["192.0.2.99"],
                "location": "Example site",
                "reason": "Demonstrate a manual draft correction",
            },
        )
        assert response.status_code == 200
        with db.tx() as tx:
            assert len(tx.list("device")) == 6
            assert len(tx.list("baseline")) == 1
            assert not tx.list("incident")
            assert not tx.list("check_result")
            assert not tx.list("execution")
            assert tx.c.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def test_normal_application_does_not_advertise_demo(client):
    assert client.get("/api/v1/setup/status").json()["demo_mode"] is False
