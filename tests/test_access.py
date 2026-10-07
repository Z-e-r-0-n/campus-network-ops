import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from test_security import configured

from backend.config import Settings
from backend.db import digest, now
from backend.discovery.access import DirectReader, scan_host
from backend.discovery.keys import prepare_profile_key
from backend.discovery.parsers import cdp_neighbors, cisco_identity, dlink_identity, lldp_neighbors
from backend.discovery.sources import CombinedReader
from backend.domain.models import AccessProfile, ConnectorRequest
from backend.workflows.runtime import Activities


def request(client, path, body, revision=None):
    headers = {"Idempotency-Key": str(uuid4())}
    if revision:
        headers["If-Match"] = str(revision)
    return client.post("/api/v1" + path, json=body, headers=headers)


def profile(db, **extra):
    with db.tx(write=True) as tx:
        return tx.put(
            "access_profile",
            {
                **AccessProfile(
                    name="Test access", username="netops", prefixes=["192.0.2.0/24"]
                ).model_dump(),
                "key_ref": "test_key",
                "generate_key": True,
                "key_status": "queued",
                **extra,
            },
        )


def test_seed_does_not_require_monitoring(db, client):
    configured(db, client)
    result = request(
        client, "/discovery-runs", {"seeds": ["192.0.2.1"], "scope": {"prefixes": ["192.0.2.0/24"]}}
    )
    assert result.status_code == 200
    run = result.json()
    outcome = Activities(db, client.app.state.settings).observe(
        {"kind": "discovery", "payload": {"run_id": run["id"]}}
    )
    assert outcome["status"] == "partial"
    with db.tx() as tx:
        device = tx.list("device")[0]
        assert device["addresses"] == ["192.0.2.1"]
        assert device["role"] == "unknown" and device["status"] == "draft"
        assert tx.get("discovery", run["id"])["gaps"]
        assert not tx.list("baseline")


@pytest.mark.parametrize("algorithm", ["ed25519", "rsa3072"])
def test_worker_generates_private_key_and_only_publishes_public_material(db, tmp_path, algorithm):
    p = profile(db, key_algorithm=algorithm)
    settings = Settings(state=tmp_path)
    result = prepare_profile_key(db, settings, p["id"])
    assert result["status"] == "ready"
    material = (tmp_path / "secrets/test_key").read_bytes()
    assert (tmp_path / "secrets/test_key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "secrets").stat().st_mode & 0o777 == 0o700
    key = serialization.load_ssh_private_key(material, None)
    with db.tx() as tx:
        record = tx.get("access_profile", p["id"])
        assert record["public_key"].startswith("ssh-ed25519" if algorithm == "ed25519" else "ssh-rsa")
        assert "PRIVATE KEY" not in json.dumps(record)
        assert tx.list("device") == []  # Generating a key cannot invent enrollment.
    if algorithm == "rsa3072":
        assert key.key_size == 3072
    prepare_profile_key(db, settings, p["id"])
    assert (tmp_path / "secrets/test_key").read_bytes() == material


def test_key_storage_rejects_symlink(db, tmp_path):
    target = tmp_path / "unrelated"
    target.write_text("must remain unchanged")
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700)
    (directory / "test_key").symlink_to(target)
    p = profile(db)
    assert prepare_profile_key(db, Settings(state=tmp_path), p["id"])["status"] == "unavailable"
    assert target.read_text() == "must remain unchanged"


def test_no_authentication_before_trust_or_when_held(db, tmp_path, monkeypatch):
    p = profile(db, key_status="ready")
    reader = DirectReader(db, Settings(state=tmp_path), [p["id"]])
    monkeypatch.setattr(reader, "read", lambda *args: pytest.fail("Unreviewed device was authenticated"))
    assert "fingerprint" in reader("192.0.2.1")["gap"]
    with db.tx(write=True) as tx:
        tx.put("host_trust", {"address": "192.0.2.1", "profile_id": p["id"], "status": "approved"})
        tx.put(
            "access_hold",
            {"reason": "failed"},
            digest({"address": "192.0.2.1", "profile_id": p["id"], "profile_revision": p["revision"]}),
        )
    assert "held" in reader("192.0.2.1")["gap"]
    assert "Map one" in reader("198.51.100.1")["gap"]


def test_host_scan_is_public_only_and_approval_binds_revision(db, client, monkeypatch):
    configured(db, client)
    p = profile(db, key_status="ready")
    public = (
        ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="192.0.2.1 " + public + "\n")

    monkeypatch.setattr("backend.discovery.access.subprocess.run", run)
    rejected = request(client, "/host-identities", {"profile_id": p["id"], "address": "198.51.100.1"})
    assert rejected.status_code == 422 and not calls
    created = request(client, "/host-identities", {"profile_id": p["id"], "address": "192.0.2.1"}).json()
    scan_host(db, created["id"])
    assert calls[0][0] == "ssh-keyscan" and not any("password" in a for a in calls[0])
    with db.tx() as tx:
        record = tx.get("host_trust", created["id"])
    body = {
        "fingerprint_digest": record["fingerprint_digest"],
        "basis": "accepted_first_use",
        "note": "Test fixture approval",
    }
    assert (
        request(
            client,
            f"/host-identities/{record['id']}/approve",
            {**body, "fingerprint_digest": "0" * 64},
            record["revision"],
        ).status_code
        == 409
    )
    assert (
        request(client, f"/host-identities/{record['id']}/approve", body, record["revision"] - 1).status_code
        == 409
    )
    accepted = request(client, f"/host-identities/{record['id']}/approve", body, record["revision"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["identity_verified"] is False
    scan_host(db, record["id"])
    assert len(calls) == 1  # Temporal retry cannot replace approved keys.


def test_expired_host_scan_cannot_be_approved(db, client):
    configured(db, client)
    p = profile(db, key_status="ready")
    with db.tx(write=True) as tx:
        host = tx.put(
            "host_trust",
            {
                "address": "192.0.2.1",
                "profile_id": p["id"],
                "profile_revision": p["revision"],
                "status": "awaiting_review",
                "keys": ["public-test-key"],
                "fingerprint_digest": digest(["public-test-key"]),
                "observed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            },
        )
    assert (
        request(
            client,
            f"/host-identities/{host['id']}/approve",
            {
                "fingerprint_digest": host["fingerprint_digest"],
                "basis": "independently_verified",
                "note": "Expired fixture",
            },
            host["revision"],
        ).status_code
        == 409
    )


def test_combined_sources_keep_neighbor_poll_times():
    def direct(address):
        return {"source": "ssh", "observed_at": now(), "serial": "LIVE", "neighbors": []}

    old = "2020-01-01T00:00:00+00:00"

    def monitored(address):
        return {
            "source": "librenms",
            "observed_at": old,
            "serial": "OLD",
            "neighbors": [{"address": "192.0.2.2"}],
        }

    result = CombinedReader(direct, monitored)("192.0.2.1")
    assert result["serial"] == "LIVE"
    assert result["neighbors"][0]["observed_at"] == old
    assert len(result["observations"]) == 2


def test_unverified_source_timezone_and_no_site_http_exception():
    from backend.connectors.librenms import poll_time

    assert poll_time("2026-01-01 10:00:00", None) is None
    assert poll_time("2026-01-01T10:00:00+05:30", None).endswith("04:30:00+00:00")
    values = dict(
        name="Test",
        kind="librenms",
        endpoint="http://monitor.example/api/v0",
        pinned_address="10.0.0.1",
        secret_ref="test",
    )
    with pytest.raises(ValueError):
        ConnectorRequest(**values)
    assert ConnectorRequest(**values, allow_private_http=True).allow_private_http


def test_vendor_parsers_extract_observations_without_assigning_site_roles():
    text = "Cisco IOS XE Software, Version 17.09.04a\nedge-a uptime is 3 days\nProcessor board ID TESTSERIAL\nModel Number : C9200-24T\n"
    result = cisco_identity(text)
    assert result == {
        "platform": "cisco_iosxe",
        "serial": "TESTSERIAL",
        "firmware": "17.09.04a",
        "model": "C9200-24T",
        "label": "edge-a",
    }
    cdp = "Device ID: edge-b\n  IP address: 192.0.2.2\nInterface: GigabitEthernet1/0/1, Port ID (outgoing port): GigabitEthernet0/1\n"
    assert cdp_neighbors(cdp)[0]["address"] == "192.0.2.2"
    assert cdp_neighbors(cdp)[0]["local_port"] == "GigabitEthernet1/0/1"
    lldp = "Local Intf: Gi1/0/2\nPort id: Gi0/1\nSystem Name: other-edge\nManagement Addresses:\n    IP: 192.0.2.3\n"
    assert lldp_neighbors(lldp)[0]["address"] == "192.0.2.3"
    assert (
        dlink_identity(
            "System Name : switch-a\nDevice Type : DGS-EXAMPLE\nFirmware Version : 1.2\nSerial Number : DEMO"
        )["serial"]
        == "DEMO"
    )
    assert "role" not in result
