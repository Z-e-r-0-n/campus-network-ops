from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from backend.checks.collect import correlate, run_check, tcp
from backend.checks.evaluate import evaluate
from backend.db import digest, now
from tests.test_check_workflow import BINDING, approved_policy


def emit(db, second, status="failed", *, policy_fingerprint="reviewed-policy"):
    instant = datetime(2026, 9, 30, 10, tzinfo=UTC) + timedelta(seconds=second)
    policy = {
        "id": "policy",
        "check_id": "H01",
        "source_id": "source",
        "target_id": "target",
        "interval_seconds": 60,
    }
    with db.tx(write=True) as tx:
        check = tx.put(
            "check_result",
            {
                "status": status,
                "observed_at": instant.isoformat(),
                "summary": "Measured window",
                "evidence_ids": [],
                "policy_fingerprint": policy_fingerprint,
            },
        )
        correlate(tx, policy, check)


def test_repeated_and_out_of_order_samples_cannot_open_or_resolve_incident(db):
    for _ in range(5):
        emit(db, 0)
    emit(db, -60)
    with db.tx() as tx:
        assert tx.list("check_streak")[0]["failed"] == 1
        assert not tx.list("incident")
    emit(db, 60)
    emit(db, 120)
    with db.tx() as tx:
        assert tx.list("incident")[0]["status"] == "open"
    for _ in range(5):
        emit(db, 180, "passed")
    with db.tx() as tx:
        assert tx.list("incident")[0]["status"] == "open"
    emit(db, 240, "passed")
    emit(db, 300, "passed")
    with db.tx() as tx:
        assert tx.list("incident")[0]["status"] == "resolved"


def test_missing_window_and_changed_policy_break_failure_streak(db):
    emit(db, 0)
    emit(db, 60)
    emit(db, 300)
    with db.tx() as tx:
        assert tx.list("check_streak")[0]["failed"] == 1
        assert not tx.list("incident")
    emit(db, 360, policy_fingerprint="new-review")
    with db.tx() as tx:
        assert tx.list("check_streak")[0]["failed"] == 1


def test_unknown_measurement_interrupts_the_streak(db):
    emit(db, 0)
    emit(db, 60)
    emit(db, 120, "unknown")
    emit(db, 180)
    with db.tx() as tx:
        assert tx.list("check_streak")[0]["failed"] == 1
        assert not tx.list("incident")


def records(db, enabled=True, status="accepted"):
    with db.tx(write=True) as tx:
        tx.put("device", {"status": status, "addresses": ["192.0.2.1"]}, "source")
        tx.put("device", {"status": status, "addresses": ["192.0.2.2"]}, "target")
        tx.put("probe", {"verified_local": True, "source_address": "192.0.2.1"}, "source")
        policy = tx.put(
            "check_policy",
            {
                "source_id": "source",
                "target_id": "target",
                "check_id": "H01",
                "enabled": enabled,
                "parameters": {},
                "interval_seconds": 60,
            },
        )
    return policy


def test_disabled_and_unreviewed_paths_never_dispatch_probe(db):
    with patch("backend.checks.collect.ping", side_effect=AssertionError("must not dispatch")):
        disabled = records(db, enabled=False)
        assert run_check(db, disabled["id"])["reason"] == "policy_disabled"
        draft = records(db, status="draft")
        assert run_check(db, draft["id"])["reason"] == "unreviewed_path"


def test_policy_disabled_during_probe_cannot_report_health(db):
    policy = approved_policy(db)

    def probe(*args):
        with db.tx(write=True) as tx:
            tx.put("check_policy", {**tx.get("check_policy", policy["id"]), "enabled": False}, policy["id"])
        return {"observed_at": now(), "sent": 10, "rtt_ms": [1] * 10}

    with (
        patch("backend.checks.collect.ping", side_effect=probe),
        patch("backend.checks.collect.local_binding", return_value=BINDING),
    ):
        result = run_check(db, policy["id"])
    assert result["status"] == "unknown"
    assert result["reason"] == "policy_or_path_changed_during_collection"


def test_cached_normalized_sample_cannot_become_three_windows(db):
    policy = records(db)
    with db.tx(write=True) as tx:
        tx.put("probe", {"verified_local": False}, "source")
        tx.put(
            "measurement",
            {"evidence": {"observed_at": now(), "sent": 10, "rtt_ms": []}},
            digest({"check": "H01", "source": "source", "target": "target"}),
        )
    for _ in range(3):
        assert run_check(db, policy["id"])["status"] == "failed"
    with db.tx() as tx:
        assert tx.list("check_streak")[0]["failed"] == 1
        assert not tx.list("incident")


def test_tcp_success_does_not_claim_application_health():
    with patch("backend.checks.collect.socket.socket"):
        evidence = tcp("192.0.2.1", "192.0.2.2", 443)
    assert evidence["tcp_connected"] is True
    result = evaluate("H04", evidence)
    assert result["status"] == "unknown"
    assert result["reason"] == "application_response_unmeasured"


def test_bad_source_binding_is_coverage_gap_not_target_failure():
    with patch("backend.checks.collect.socket.socket") as socket:
        connection = socket.return_value.__enter__.return_value
        connection.bind.side_effect = OSError("Cannot assign requested address")
        evidence = tcp("192.0.2.1", "192.0.2.2", 443)
        connection.connect.assert_not_called()
    assert evaluate("H04", evidence)["status"] == "unknown"
    assert evidence["reason"] == "source_binding_unavailable"
