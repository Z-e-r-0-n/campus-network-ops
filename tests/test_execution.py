from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.db import Conflict, digest, now
from backend.execution.runner import ExecutionRunner


class Isolation:
    def verify(self, target, addresses):
        return target == "switch" and addresses == ["192.0.2.1"]

    def previous_attempt_stopped(self, owner):
        return False


class Adapter:
    def __init__(self):
        self.state = "not_applied"
        self.saved = False
        self.backups = 0
        self.applies = 0
        self.persists = 0
        self.recoveries = 0
        self.fail_services = False
        self.apply_hook = lambda: None
        self.service_hook = lambda: None
        self.close_error = False

    def inspect(self):
        return {"configuration_digest": "snapshot"}

    def reconcile(self, content, live):
        return self.state

    def identity_matches(self, expected, actual):
        return True

    def backup(self):
        self.backups += 1
        return {"verified": True, "reference": "original-backup"}

    def verify_backup(self, backup):
        return backup["reference"] == "original-backup"

    def recovery_ready(self, content):
        return True

    def apply(self, action, content):
        self.applies += 1
        self.state = "applied"
        self.apply_hook()

    def recover(self, recovery, content):
        self.recoveries += 1
        self.state = "not_applied"

    def verify_services(self, policy, seconds, recovering=False):
        self.service_hook()
        return recovering or not self.fail_services

    def persist(self, content, recovering=False):
        self.persists += 1
        self.saved = True

    def verify_persistence(self, content):
        return self.saved

    def verify_recovery(self, content):
        return self.state == "not_applied" and self.saved

    def close(self):
        if self.close_error:
            raise RuntimeError("private transport detail must not appear in records")


def accepted_policy(url, facts):
    assert facts["target_count"] == facts["action_count"] == 1
    return not facts["paused"] and all(v for k, v in facts.items() if k != "paused")


@pytest.fixture
def case(db):
    content = {
        "device_id": "switch",
        "device_revision": 1,
        "device_identity": {"addresses": ["192.0.2.1"], "serial": "example", "firmware": "test"},
        "adapter": "fixture",
        "adapter_version": "1",
        "snapshot_digest": "snapshot",
        "actions": [{"type": "service.replace_servers", "parameters": {}}],
        "limits": {"max_duration_seconds": 300},
        "observation_seconds": 1,
        "service_verification_policy": {"required": ["ntp"]},
        "recovery": {"before": {}},
    }
    with db.tx(write=True) as tx:
        tx.put("device", {"status": "accepted"}, "switch")
        tx.put(
            "qualification",
            {
                "adapter": "fixture",
                "adapter_version": "1",
                "firmware": "test",
                "actions": ["service.replace_servers"],
            },
            "switch",
        )
        tx.put("plan", {"content": content, "digest": digest(content), "status": "approved"}, "plan")
        tx.put(
            "approval",
            {
                "plan_id": "plan",
                "digest": digest(content),
                "revoked": False,
                "expires": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            },
            "approval",
        )
        tx.put(
            "execution",
            {
                "plan_id": "plan",
                "approval_id": "approval",
                "digest": digest(content),
                "status": "queued",
                "steps": [],
                "stop_requested": False,
            },
            "execution",
        )
    adapter = Adapter()
    runner = ExecutionRunner(
        db,
        SimpleNamespace(mutations_enabled=True, opa="unused"),
        lambda *args: adapter,
        Isolation(),
        accepted_policy,
    )
    return runner, adapter


def change(db, kind, key, **values):
    with db.tx(write=True) as tx:
        return tx.put(kind, {**tx.get(kind, key), **values}, key)


def interrupt_after_apply(case):
    runner, adapter = case

    def interrupted():
        raise RuntimeError("private adapter error after command dispatch")

    adapter.apply_hook = interrupted
    assert runner.run("execution")["status"] == "outcome_unknown"
    adapter.apply_hook = lambda: None


def test_verified_and_repeat_delivery_is_noop(case):
    runner, adapter = case
    first = runner.run("execution")
    assert first["status"] == "verified"
    assert first["started_at"] < first["deadline_at"]
    assert runner.run("execution") == first
    assert (adapter.backups, adapter.applies, adapter.persists) == (1, 1, 1)


def test_resume_preserves_backup_deadline_and_does_not_reapply(case, db):
    runner, adapter = case
    interrupt_after_apply(case)
    with db.tx() as tx:
        original = tx.get("execution", "execution")
    result = runner.run("execution")
    assert result["status"] == "verified"
    assert result["deadline_at"] == original["deadline_at"]
    assert result["backup"] == original["backup"]
    assert (adapter.applies, adapter.backups, adapter.persists) == (1, 1, 1)
    assert "private adapter error" not in str(result)


def test_unchanged_readback_after_dispatch_does_not_allow_retry(case):
    runner, adapter = case
    interrupt_after_apply(case)
    adapter.state = "not_applied"
    assert runner.run("execution")["status"] == "outcome_unknown"
    assert adapter.applies == 1
    assert adapter.persists == 0


def test_expired_resume_cannot_extend_its_deadline(case, db):
    runner, adapter = case
    interrupt_after_apply(case)
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    change(db, "execution", "execution", deadline_at=expired)
    result = runner.run("execution")
    assert result["status"] == "manual_recovery_required"
    assert result["deadline_at"] == expired
    assert adapter.backups == 1
    assert adapter.persists == adapter.recoveries == 0


@pytest.mark.parametrize("control", ["stop", "pause", "revoke", "qualification", "device", "lease"])
def test_control_changes_after_apply_prevent_further_writes(case, db, control):
    runner, adapter = case

    def altered():
        if control == "stop":
            change(db, "execution", "execution", stop_requested=True)
        elif control == "revoke":
            change(db, "approval", "approval", revoked=True)
        elif control == "qualification":
            change(db, "qualification", "switch", adapter_version="2")
        elif control == "device":
            change(db, "device", "switch", status="draft")
        elif control == "pause":
            with db.tx(write=True) as tx:
                tx.put("system", {"active": True}, "execution_pause")
        else:
            with db.tx(write=True) as tx:
                tx.c.execute("UPDATE device_leases SET fence=fence+1")

    adapter.apply_hook = altered
    assert runner.run("execution")["status"] == "manual_recovery_required"
    assert adapter.persists == adapter.recoveries == 0


def test_pause_during_service_observation_prevents_save(case, db):
    runner, adapter = case

    def pause():
        with db.tx(write=True) as tx:
            tx.put("system", {"active": True}, "execution_pause")

    adapter.service_hook = pause
    assert runner.run("execution")["status"] == "manual_recovery_required"
    assert adapter.persists == 0


def test_service_failure_runs_approved_recovery_and_verifies(case):
    runner, adapter = case
    adapter.fail_services = True
    result = runner.run("execution")
    assert result["status"] == "recovered"
    assert adapter.recoveries == adapter.persists == 1


def test_interrupted_persistence_is_not_repeated(case):
    runner, adapter = case

    def uncertain_save(content, recovering=False):
        adapter.persists += 1
        adapter.saved = True
        raise OSError("reply lost")

    adapter.persist = uncertain_save
    assert runner.run("execution")["status"] == "outcome_unknown"
    assert runner.run("execution")["status"] == "verified"
    assert adapter.persists == 1


def test_unverified_interrupted_recovery_does_not_repeat(case, db):
    runner, adapter = case
    interrupt_after_apply(case)
    with db.tx(write=True) as tx:
        execution = tx.get("execution", "execution")
        tx.put(
            "execution",
            {**execution, "steps": execution["steps"] + [{"step": "recovery_intent", "at": now()}]},
            "execution",
        )
    assert runner.run("execution")["status"] == "manual_recovery_required"
    assert adapter.recoveries == adapter.persists == 0


def test_duplicate_attempt_cannot_steal_same_execution_lease(case):
    runner, adapter = case

    def duplicate():
        with pytest.raises(Conflict, match="holds the device lease"):
            runner.run("execution")

    adapter.apply_hook = duplicate
    assert runner.run("execution")["status"] == "verified"
    assert adapter.applies == 1


def test_abandoned_attempt_requires_launcher_confirmation(case, db):
    runner, adapter = case
    with db.tx(write=True) as tx:
        tx.c.execute("INSERT INTO device_leases VALUES('switch','abandoned',1,now()-interval '1 second')")
    with pytest.raises(Conflict, match="launcher"):
        runner.run("execution")
    assert adapter.backups == adapter.applies == 0


def test_close_failure_releases_lease_but_does_not_claim_attempt_stopped(case, db):
    runner, adapter = case
    adapter.close_error = True
    assert runner.run("execution")["status"] == "verified"
    with db.tx() as tx:
        lease = tx.c.execute("SELECT *, until_at<=now() AS expired FROM device_leases").fetchone()
        assert lease["expired"]
        assert tx.get("execution_attempt", lease["owner"])["closed"] is False


def test_revocation_during_policy_check_stops_before_dispatch(case, db):
    runner, adapter = case

    def revoke_during_policy(url, facts):
        change(db, "approval", "approval", revoked=True)
        return True

    runner.policy = revoke_during_policy
    result = runner.run("execution")
    assert result["status"] == "failed"
    assert adapter.applies == adapter.persists == 0
    assert not any(s.get("step") == "action_intent" for s in result["steps"])


def test_expired_approval_does_not_open_device(case, db):
    runner, adapter = case
    change(db, "approval", "approval", expires=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    assert runner.run("execution")["status"] == "failed"
    assert adapter.backups == adapter.applies == 0


def test_plan_tampering_is_rejected_before_any_device_read(case, db):
    runner, adapter = case
    with db.tx(write=True) as tx:
        plan = tx.get("plan", "plan")
        content = {**plan["content"], "observation_seconds": 0}
        tx.put("plan", {**plan, "content": content}, "plan")
    assert runner.run("execution")["status"] == "failed"
    assert adapter.backups == adapter.applies == 0
