import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.security.auth import Auth, hashed
from backend.security.redaction import redact


def configured(db, client):
    token = secrets.token_urlsafe(32)
    with db.tx(write=True) as tx:
        tx.put(
            "system",
            {
                "token_hash": hashed(token),
                "expires": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            },
            "bootstrap",
        )
    response = client.post(
        "/api/v1/setup/administrator",
        json={"username": "admin", "password": "Only-for-automated-tests-123", "bootstrap_token": token},
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            "/api/v1/session", json={"username": "admin", "password": "Only-for-automated-tests-123"}
        ).status_code
        == 200
    )
    client.headers["X-CSRF-Token"] = client.cookies["campus_csrf"]
    client.headers["Idempotency-Key"] = str(uuid4())


def test_session_csrf_and_idempotency(db, client):
    assert client.get("/api/v1/network").status_code == 401
    configured(db, client)
    body = {
        "label": "Building switch",
        "role": "access",
        "addresses": ["192.0.2.1"],
        "reason": "Manual infrastructure record",
    }
    first = client.post("/api/v1/devices", json=body)
    assert first.status_code == 200
    assert client.post("/api/v1/devices", json=body).json()["id"] == first.json()["id"]
    assert client.post("/api/v1/devices", json={**body, "label": "Different"}).status_code == 409
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/v1/devices", json=body).status_code == 403
    assert client.get("/api/v1/devices").json()["items"][0]["label"] == "Building switch"


def test_login_throttle_and_no_password_echo(db, client):
    for _ in range(8):
        assert (
            client.post("/api/v1/session", json={"username": "nobody", "password": "wrong"}).status_code
            == 401
        )
    assert client.post("/api/v1/session", json={"username": "nobody", "password": "wrong"}).status_code == 429
    marker = "sensitive-marker" * 40
    response = client.post("/api/v1/session", json={"username": "admin", "password": marker})
    assert response.status_code == 422
    assert marker not in response.text


def test_origin_and_logout(db, client):
    configured(db, client)
    client.headers["Origin"] = "https://untrusted.example"
    assert client.delete("/api/v1/session").status_code == 403
    client.headers["Origin"] = "http://testserver"
    assert client.delete("/api/v1/session").status_code == 200
    assert client.get("/api/v1/session").status_code == 401


def test_password_change_revokes_other_sessions(db, client):
    configured(db, client)
    token, _ = Auth(db).login("admin", "Only-for-automated-tests-123", "other-test-source")
    response = client.post(
        "/api/v1/account/password",
        json={"password": "Only-for-automated-tests-123", "new_password": "Replacement-for-tests-123"},
    )
    assert response.status_code == 200
    with db.tx() as tx:
        assert tx.c.execute("SELECT 1 FROM sessions WHERE token_hash=%s", (hashed(token),)).fetchone() is None


def test_redaction():
    data = {
        "snmp_community": "must disappear",
        "message": "username operator secret 5 not-for-provider\nport is up",
    }
    clean = redact(data)
    assert "must disappear" not in str(clean)
    assert "not-for-provider" not in str(clean)
    assert "port is up" in str(clean)
