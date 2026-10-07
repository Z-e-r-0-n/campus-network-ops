from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.checks.collect import run_check
from backend.checks.policies import approve, enqueue, prepare
from backend.checks.probes import local_binding, verify_probe
from backend.db import Conflict, now
from backend.domain.models import Approval
from backend.workflows.runtime import schedule
from tests.test_security import configured

BINDING = {"worker": "acceptance-worker", "interface": "test0", "index": 1, "address": "192.0.2.1"}


def approved_policy(db, check_id="H01", **parameters):
    with db.tx(write=True) as tx:
        for name, address in [("source", "192.0.2.1"), ("target", "192.0.2.2")]:
            tx.put(
                "device", {"status": "accepted", "addresses": [address], "label": name, "role": "probe"}, name
            )
        tx.put("probe", {"verified_local": True, "source_address": "192.0.2.1", "binding": BINDING}, "source")
        policy = tx.put(
            "check_policy",
            {
                "source_id": "source",
                "target_id": "target",
                "target_address": "192.0.2.2",
                "check_id": check_id,
                "parameters": {"max_loss_percent": 0, "max_rtt_ms": 50, **parameters},
                "interval_seconds": 60,
                "enabled": False,
                "next_due": now(),
            },
        )
        review = prepare(tx, policy["id"], "test")
        return approve(
            tx, review["id"], Approval(digest=review["digest"], revision=review["revision"]), "test"
        )


def post(client, path, body=None, revision=None):
    client.headers["Idempotency-Key"] = str(uuid4())
    if revision:
        client.headers["If-Match"] = str(revision)
    return client.post("/api/v1" + path, json=body or {})


def test_api_from_source_verification_to_approved_measurement_and_pause(db, client):
    configured(db, client)
    with db.tx(write=True) as tx:
        for name, address in [("source", "192.0.2.1"), ("target", "192.0.2.2")]:
            tx.put(
                "device", {"status": "accepted", "addresses": [address], "label": name, "role": "probe"}, name
            )
    request = post(client, "/probe-requests", {"source_id": "source", "source_address": "192.0.2.1"})
    assert request.status_code == 200
    with patch("backend.checks.probes.local_binding", return_value=BINDING):
        assert verify_probe(db, request.json()["id"])["status"] == "verified"
    body = {
        "source_id": "source",
        "target_id": "target",
        "target_address": "192.0.2.2",
        "check_id": "H01",
        "parameters": {"max_loss_percent": 0},
        "interval_seconds": 60,
    }
    assert post(client, "/check-policies", {**body, "enabled": True}).status_code == 422
    policy = post(client, "/check-policies", body).json()
    assert post(client, f"/check-policies/{policy['id']}/run").status_code == 409
    review = post(client, f"/check-policies/{policy['id']}/review").json()
    enabled = post(
        client,
        f"/check-reviews/{review['id']}/approve",
        {"digest": review["digest"], "revision": review["revision"]},
    )
    assert enabled.status_code == 200
    run = post(client, f"/check-policies/{policy['id']}/run").json()
    with (
        patch("backend.checks.collect.local_binding", return_value=BINDING),
        patch(
            "backend.checks.collect.ping", return_value={"observed_at": now(), "sent": 10, "rtt_ms": [1] * 10}
        ) as ping,
    ):
        result = run_check(db, policy["id"], run["id"])
        assert result["status"] == "passed"
        assert run_check(db, policy["id"], run["id"])["id"] == result["id"]
        assert ping.call_count == 1
    card = client.get("/api/v1/measurements").json()["items"][0]
    assert card["latest_result"]["id"] == result["id"]
    assert card["active_run"]["status"] == "complete"
    assert post(client, f"/check-policies/{policy['id']}/run").status_code == 409
    assert post(client, f"/check-policies/{policy['id']}/pause", revision=card["revision"]).status_code == 200
    with patch("backend.checks.collect.ping", side_effect=AssertionError("paused")):
        assert run_check(db, policy["id"])["reason"] == "policy_disabled"


def test_source_review_has_no_network_traffic_and_rejects_wrong_host(db):
    policy = approved_policy(db)
    with db.tx(write=True) as tx:
        request = tx.put(
            "probe_request", {"status": "queued", "source_id": "source", "source_address": "192.0.2.1"}
        )
        tx.put("probe", {"verified_local": False, "request_id": request["id"]}, "source")
    with patch("backend.checks.probes.local_binding", side_effect=ValueError("not local")):
        assert verify_probe(db, request["id"])["status"] == "unavailable"
    with db.tx() as tx:
        assert not tx.get("probe", "source")["verified_local"]
    with patch("backend.checks.collect.ping", side_effect=AssertionError("wrong source")):
        assert run_check(db, policy["id"])["status"] == "unknown"


def test_approval_requires_reviewed_threshold_and_current_path(db):
    policy = approved_policy(db)
    with db.tx(write=True) as tx:
        tx.put("check_policy", {**policy, "parameters": {}}, policy["id"])
    with db.tx(write=True) as tx, pytest.raises(ValueError, match="packet loss"):
        prepare(tx, policy["id"], "test")
    with db.tx(write=True) as tx:
        tx.put("check_policy", {**policy, "enabled": False}, policy["id"])
        review = prepare(tx, policy["id"], "test")
        target = tx.get("device", "target")
        tx.put("device", {**target, "addresses": ["192.0.2.3"]}, "target")
    with db.tx(write=True) as tx, pytest.raises(Conflict):
        approve(tx, review["id"], Approval(digest=review["digest"], revision=review["revision"]), "test")


def test_scheduler_and_manual_requests_share_active_run_after_dispatch(db):
    policy = approved_policy(db)
    schedule(db)
    with db.tx(write=True) as tx:
        current = tx.get("check_policy", policy["id"])
        tx.c.execute("UPDATE outbox SET dispatched=now()")
        same = enqueue(tx, current)
        assert same["id"] == current["active_run_id"]
        tx.put("check_policy", {**tx.get("check_policy", policy["id"]), "next_due": now()}, policy["id"])
    schedule(db)
    with db.tx() as tx:
        assert len(tx.list("check_run")) == 1
        assert tx.c.execute("SELECT count(*) AS n FROM outbox WHERE kind='check'").fetchone()["n"] == 1


def test_expired_or_replaced_jobs_cannot_send_packets(db):
    policy = approved_policy(db)
    with db.tx(write=True) as tx:
        run = enqueue(tx, policy)
        tx.put(
            "check_run",
            {**run, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            run["id"],
        )
    with patch("backend.checks.collect.ping", side_effect=AssertionError("expired")):
        assert run_check(db, policy["id"], run["id"])["status"] == "expired"


def test_changed_worker_binding_never_dispatches(db):
    policy = approved_policy(db)
    with (
        patch("backend.checks.collect.local_binding", return_value={**BINDING, "worker": "new-host"}),
        patch("backend.checks.collect.ping", side_effect=AssertionError("wrong worker")),
    ):
        assert run_check(db, policy["id"])["reason"] == "probe_or_approval_needs_review"


def test_rolling_latency_collects_distribution_and_preserves_evidence(db):
    policy = approved_policy(db, check_id="H02")
    start = datetime.now(UTC) - timedelta(seconds=90)
    with patch("backend.checks.collect.local_binding", return_value=BINDING):
        for i in range(10):
            with patch(
                "backend.checks.collect.ping",
                return_value={
                    "observed_at": (start + timedelta(seconds=i * 10)).isoformat(),
                    "sent": 10,
                    "rtt_ms": [i + 1] * 10,
                },
            ):
                result = run_check(db, policy["id"])
            assert result["status"] == ("passed" if i == 9 else "unknown")
        assert result["metrics"]["received"] == 100
        assert result["metrics"]["p95_rtt_ms"] == 10
        assert len(result["evidence_ids"]) == 10
        # An overlapping distribution is not a second independent passing window.
        with patch(
            "backend.checks.collect.ping", return_value={"observed_at": now(), "sent": 10, "rtt_ms": [5] * 10}
        ):
            run_check(db, policy["id"])
    with db.tx() as tx:
        assert tx.list("check_streak")[0]["passed"] == 1


def test_verification_checks_real_loopback_without_scanning():
    binding = local_binding("127.0.0.1")
    assert binding["interface"] == "lo"
    assert len(binding["worker"]) == 64


def test_device_measurement_budget_defers_overlapping_paths_without_traffic(db):
    from backend.checks.collect import measurement_slot

    policy = approved_policy(db)
    with db.tx(write=True) as tx:
        run = enqueue(tx, policy)
    with (
        measurement_slot(db, "source", "another-target", "other-attempt"),
        patch("backend.checks.collect.local_binding", return_value=BINDING),
        patch("backend.checks.collect.ping", side_effect=AssertionError("busy source")),
    ):
        result = run_check(db, policy["id"], run["id"])
    assert result["reason"] == "measurement_budget_busy"
    with db.tx() as tx:
        assert not tx.list("measurement_lease")
        current = tx.get("check_policy", policy["id"])
        assert 0 < (datetime.fromisoformat(current["next_due"]) - datetime.now(UTC)).total_seconds() <= 30


def test_scheduler_holds_changed_path_instead_of_sending_to_new_address(db):
    policy = approved_policy(db)
    with db.tx(write=True) as tx:
        target = tx.get("device", "target")
        tx.put("device", {**target, "addresses": ["192.0.2.3"]}, "target")
    schedule(db)
    with db.tx() as tx:
        current = tx.get("check_policy", policy["id"])
        assert not current["enabled"]
        assert current["hold_reason"]
        assert not tx.list("check_run")


def test_reapproval_respects_existing_traffic_cadence(db):
    policy = approved_policy(db)
    with db.tx(write=True) as tx:
        enqueue(tx, policy)
        current = tx.get("check_policy", policy["id"])
        paused = tx.put("check_policy", {**current, "enabled": False, "approval_id": None}, policy["id"])
        review = prepare(tx, paused["id"], "test")
        enabled = approve(
            tx, review["id"], Approval(digest=review["digest"], revision=review["revision"]), "test"
        )
        assert enabled["next_due"] == current["next_eligible_at"]
    schedule(db)
    with db.tx() as tx:
        assert tx.get("check_policy", policy["id"])["enabled"]


def test_identity_exclusion_pauses_measurements_and_restore_requires_review(db, client):
    configured(db, client)
    policy = approved_policy(db)
    with db.tx() as tx:
        device = tx.get("device", "target")
    excluded = post(
        client,
        "/devices/target/disposition",
        {"status": "excluded", "reason": "Out of monitoring scope"},
        device["revision"],
    )
    assert excluded.status_code == 200
    assert client.get("/api/v1/excluded-devices").json()["items"][0]["id"] == "target"
    with db.tx() as tx:
        current = tx.get("check_policy", policy["id"])
        assert current["enabled"] is False
        assert current["approval_id"] is None
    restored = post(
        client,
        "/devices/target/disposition",
        {"status": "draft", "reason": "Return for inventory review"},
        excluded.json()["revision"],
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "draft"
    assert client.get("/api/v1/excluded-devices").json()["items"] == []
    assert post(client, f"/check-policies/{policy['id']}/review").status_code == 409
