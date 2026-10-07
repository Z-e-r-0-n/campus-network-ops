"""Durable, fenced execution of one typed, approved device action.

The trusted launcher must enforce target egress and confirm termination of abandoned
attempts. A database lease alone cannot fence a command already sent over SSH.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.db import Conflict, digest, now
from backend.execution.policy import permitted

TERMINAL = {"verified", "recovered", "failed", "manual_recovery_required"}


class BoundaryClosed(Exception):
    pass


class UncertainWrite(Exception):
    pass


class ExecutionRunner:
    def __init__(self, store, settings, adapter_factory, isolation, policy=permitted):
        self.store, self.settings = store, settings
        self.adapter_factory, self.isolation, self.policy = adapter_factory, isolation, policy

    def record(self, key, status, step=None, **fields):
        with self.store.tx(write=True) as tx:
            execution = tx.get("execution", key)
            steps = execution["steps"] + ([{**step, "at": now()}] if step else [])
            result = tx.put("execution", {**execution, **fields, "status": status, "steps": steps}, key)
            tx.audit("executor", "execution." + status, key, step or {})
            if status in TERMINAL | {"outcome_unknown"}:
                plan = tx.get("plan", execution["plan_id"])
                tx.put("plan", {**plan, "status": status}, plan["id"])
            return result

    def dispatched(self, key):
        with self.store.tx() as tx:
            return any(s.get("step", "").endswith("_intent") for s in tx.get("execution", key)["steps"])

    def lease(self, target, owner):
        with self.store.tx() as tx:
            previous = tx.c.execute("SELECT * FROM device_leases WHERE device=%s", (target,)).fetchone()
            attempt = tx.get("execution_attempt", previous["owner"], False) if previous else None
        instant = datetime.now(UTC)
        if previous:
            if previous["until_at"] > instant:
                raise Conflict("Another attempt holds the device lease")
            if not (attempt and attempt.get("closed")):
                stopped = getattr(self.isolation, "previous_attempt_stopped", lambda _: False)
                if not stopped(previous["owner"]):
                    raise Conflict("The launcher must confirm that the previous attempt has stopped")
        with self.store.tx(write=True) as tx:
            old = tx.c.execute("SELECT * FROM device_leases WHERE device=%s FOR UPDATE", (target,)).fetchone()
            if old != previous:
                raise Conflict("Device lease changed while checking the previous attempt")
            fence = old["fence"] + 1 if old else 1
            tx.c.execute(
                """INSERT INTO device_leases VALUES(%s,%s,%s,%s) ON CONFLICT(device) DO UPDATE SET
                owner=EXCLUDED.owner, fence=EXCLUDED.fence, until_at=EXCLUDED.until_at""",
                (target, owner, fence, instant + timedelta(seconds=120)),
            )
            tx.put("execution_attempt", {"device_id": target, "closed": False}, owner)
            return fence

    def boundary(self, key, content, owner, fence, adapter, *, recovery=False):
        """Re-read authorization and lease at every transition, including before persistence."""
        with self.store.tx(write=True) as tx:
            execution = tx.get("execution", key)
            plan = tx.get("plan", execution["plan_id"])
            approval = tx.get("approval", execution["approval_id"])
            device = tx.get("device", content["device_id"])
            qualified = tx.get("qualification", content["device_id"], False) or {}
            pause = tx.get("system", "execution_pause", False) or {}
            if execution.get("attempt_id") != owner:
                raise BoundaryClosed("Execution attempt changed")
            deadline = datetime.fromisoformat(execution["deadline_at"])
            lease = tx.c.execute(
                """UPDATE device_leases SET until_at=now()+interval '120 seconds'
                WHERE device=%s AND owner=%s AND fence=%s AND until_at>now() RETURNING fence""",
                (content["device_id"], owner, fence),
            ).fetchone()
            valid = (
                not approval["revoked"]
                and approval["plan_id"] == plan["id"]
                and approval["digest"] == execution["digest"] == plan["digest"] == digest(content)
                and digest(plan["content"]) == plan["digest"]
                and device["revision"] == content["device_revision"]
                and device["status"] == "accepted"
            )
            stopped = execution.get("stop_requested") or pause.get("active", False)
        if not self.settings.mutations_enabled or not lease or not valid or stopped:
            raise BoundaryClosed("Execution disabled, stopped, revoked, changed or no longer leased")
        if datetime.now(UTC) >= deadline:
            raise BoundaryClosed("The approved execution deadline has elapsed")
        live = adapter.inspect()
        state = adapter.reconcile(content, live)
        facts = {
            "enabled": True,
            "paused": False,
            "approval_valid": valid,
            "digest_valid": valid,
            "identity_valid": adapter.identity_matches(content["device_identity"], live),
            "configuration_matches": state
            in ({"applied", "partial", "not_applied"} if recovery else {"applied", "not_applied"}),
            "qualified": qualified.get("adapter") == content["adapter"]
            and qualified.get("adapter_version") == content["adapter_version"]
            and qualified.get("firmware") == content["device_identity"].get("firmware")
            and all(a["type"] in qualified.get("actions", []) for a in content["actions"]),
            "backup_verified": adapter.verify_backup(execution["backup"]) is True,
            "recovery_ready": adapter.recovery_ready(content),
            "egress_enforced": self.isolation.verify(
                content["device_id"], content["device_identity"]["addresses"]
            ),
            "lease_valid": True,
            "within_deadline": datetime.now(UTC) < deadline,
            "target_count": 1,
            "action_count": len(content["actions"]),
        }
        if not self.policy(self.settings.opa, facts):
            raise BoundaryClosed("Execution policy denied the current state")
        # Inspection/policy calls can take time. Check control changes once more before returning.
        with self.store.tx() as tx:
            latest = tx.get("execution", key)
            current_approval = tx.get("approval", execution["approval_id"])
            current_plan = tx.get("plan", execution["plan_id"])
            current_device = tx.get("device", content["device_id"])
            current_qualification = tx.get("qualification", content["device_id"], False) or {}
            paused = tx.get("system", "execution_pause", False) or {}
            lease = tx.c.execute(
                "SELECT 1 FROM device_leases WHERE device=%s AND owner=%s AND fence=%s AND until_at>now()",
                (content["device_id"], owner, fence),
            ).fetchone()
        if (
            not lease
            or latest.get("attempt_id") != owner
            or latest.get("stop_requested")
            or paused.get("active")
            or current_approval != approval
            or current_plan != plan
            or current_device != device
            or current_qualification != qualified
            or datetime.now(UTC) >= deadline
        ):
            raise BoundaryClosed("Execution authorization changed during verification")
        return live

    def write(self, key, stage, content, owner, fence, adapter, call, *, recovery=False):
        self.boundary(key, content, owner, fence, adapter, recovery=recovery)
        status = "recovering" if recovery else "running"
        self.record(key, status, {"step": stage + "_intent", "attempt_id": owner, "fence": fence})
        # Any exception after dispatch is uncertain, including adapter parsing errors.
        try:
            call()
        except Exception:
            raise UncertainWrite from None
        self.record(key, status, {"step": stage + "_complete", "attempt_id": owner})

    def services(self, key, content, owner, fence, adapter, *, recovery=False):
        self.boundary(key, content, owner, fence, adapter, recovery=recovery)
        with self.store.tx() as tx:
            deadline = datetime.fromisoformat(tx.get("execution", key)["deadline_at"])
        if (deadline - datetime.now(UTC)).total_seconds() < content["observation_seconds"]:
            raise BoundaryClosed("Insufficient approved time for the full service observation")
        result = adapter.verify_services(
            content["service_verification_policy"],
            content["observation_seconds"],
            recovering=recovery,
        )
        self.boundary(key, content, owner, fence, adapter, recovery=recovery)
        return result is True

    def run(self, execution_id):
        with self.store.tx() as tx:
            execution = tx.get("execution", execution_id)
            plan = tx.get("plan", execution["plan_id"])
            approval = tx.get("approval", execution["approval_id"])
        if execution["status"] in TERMINAL:
            return execution
        content = plan["content"]
        if (
            approval["revoked"]
            or approval["plan_id"] != plan["id"]
            or approval["digest"] != execution["digest"]
            or execution["digest"] != plan["digest"]
            or digest(content) != plan["digest"]
        ):
            return self.record(
                execution_id,
                "failed" if execution["status"] == "queued" else "manual_recovery_required",
                {"reason": "Approval or plan integrity failed"},
            )
        if len(content["actions"]) != 1:
            return self.record(execution_id, "failed", {"reason": "Only single-action plans are qualified"})
        if not self.settings.mutations_enabled:
            # A disabled installation must not relabel a dispatched change as safely failed.
            if execution["status"] != "queued":
                return execution
            return self.record(execution_id, "failed", {"reason": "Installation execution is disabled"})
        owner, target = str(uuid4()), content["device_id"]
        fence = self.lease(target, owner)
        adapter, closed = None, True
        dispatched = any(s.get("step", "").endswith("_intent") for s in execution["steps"])
        try:
            with self.store.tx(write=True) as tx:
                execution = tx.get("execution", execution_id)
                if execution["status"] in TERMINAL:
                    return execution
                if not execution.get("started_at"):
                    if execution["status"] != "queued":
                        raise BoundaryClosed("Original execution timing is unavailable")
                    instant = datetime.now(UTC)
                    if datetime.fromisoformat(approval["expires"]) <= instant:
                        raise BoundaryClosed("Approval expired before execution started")
                    execution = {
                        **execution,
                        "started_at": instant.isoformat(),
                        "deadline_at": (
                            instant + timedelta(seconds=content["limits"]["max_duration_seconds"])
                        ).isoformat(),
                    }
                execution = tx.put(
                    "execution", {**execution, "attempt_id": owner, "status": "running"}, execution_id
                )
            if not self.isolation.verify(target, content["device_identity"]["addresses"]):
                raise BoundaryClosed("Target egress isolation is not established")
            adapter = self.adapter_factory(content["adapter"], content["adapter_version"], target)
            live = adapter.inspect()
            state = adapter.reconcile(content, live)
            if not execution.get("backup"):
                if dispatched or state != "not_applied":
                    raise BoundaryClosed("The original pre-change backup is unavailable")
                backup = adapter.backup()
                if backup.get("verified") is not True:
                    raise BoundaryClosed("Pre-change backup was not verified")
                execution = self.record(
                    execution_id,
                    "running",
                    {"step": "backup_verified", "backup_reference": backup["reference"]},
                    backup=backup,
                )
            if not adapter.verify_backup(execution["backup"]):
                raise BoundaryClosed("Original backup integrity check failed")
            steps = {s.get("step") for s in execution["steps"]}
            if "recovery_intent" in steps:
                # Never resend an interrupted reversal. Only readback can establish its outcome.
                self.boundary(execution_id, content, owner, fence, adapter, recovery=True)
                if adapter.verify_recovery(content) and self.services(
                    execution_id,
                    content,
                    owner,
                    fence,
                    adapter,
                    recovery=True,
                ):
                    return self.record(execution_id, "recovered", {"step": "resumed_recovery_verified"})
                return self.record(
                    execution_id,
                    "manual_recovery_required",
                    {"reason": "Interrupted recovery cannot be verified"},
                )
            if state == "partial" and "action_intent" in steps:
                return self.recover(execution_id, content, owner, fence, adapter, "Partial action readback")
            if state == "not_applied":
                if "action_intent" in steps:
                    return self.record(
                        execution_id,
                        "outcome_unknown",
                        {"reason": "An action was dispatched; unchanged readback does not authorize a retry"},
                    )
                if live["configuration_digest"] != content["snapshot_digest"]:
                    raise BoundaryClosed("Configuration changed since plan review")
                dispatched = True
                self.write(
                    execution_id,
                    "action",
                    content,
                    owner,
                    fence,
                    adapter,
                    lambda: adapter.apply(content["actions"][0], content),
                )
            elif state != "applied" or "action_intent" not in steps:
                raise BoundaryClosed("External state does not match an execution checkpoint")
            live = self.boundary(execution_id, content, owner, fence, adapter, recovery=True)
            if adapter.reconcile(content, live) != "applied":
                return self.recover(
                    execution_id, content, owner, fence, adapter, "Configuration readback failed"
                )
            self.record(execution_id, "observing", {"step": "service_verification"})
            if not self.services(execution_id, content, owner, fence, adapter):
                return self.recover(
                    execution_id, content, owner, fence, adapter, "Required service checks failed"
                )
            if "persistence_intent" in steps:
                if not adapter.verify_persistence(content):
                    raise UncertainWrite
            else:
                self.write(
                    execution_id,
                    "persistence",
                    content,
                    owner,
                    fence,
                    adapter,
                    lambda: adapter.persist(content),
                )
            if not adapter.verify_persistence(content):
                return self.recover(
                    execution_id, content, owner, fence, adapter, "Persistence verification failed"
                )
            self.boundary(execution_id, content, owner, fence, adapter)
            return self.record(execution_id, "verified", {"step": "configuration_and_service_verified"})
        except UncertainWrite:
            return self.record(
                execution_id,
                "outcome_unknown",
                {"reason": "Write was dispatched; reconcile device state before continuing"},
            )
        except BoundaryClosed as exc:
            return self.record(
                execution_id,
                "manual_recovery_required" if self.dispatched(execution_id) else "failed",
                {"reason": str(exc)},
            )
        except Exception:
            return self.record(
                execution_id,
                "outcome_unknown" if self.dispatched(execution_id) else "failed",
                {"reason": "Execution interrupted; inspect the recorded checkpoints"},
            )
        finally:
            try:
                if adapter:
                    adapter.close()
            except Exception:
                closed = False
            finally:
                with self.store.tx(write=True) as tx:
                    tx.put(
                        "execution_attempt",
                        {"device_id": target, "execution_id": execution_id, "closed": closed},
                        owner,
                    )
                    tx.c.execute(
                        "UPDATE device_leases SET until_at=now() WHERE device=%s AND owner=%s AND fence=%s",
                        (target, owner, fence),
                    )

    def recover(self, key, content, owner, fence, adapter, reason):
        self.record(key, "recovering", {"reason": reason})
        live = self.boundary(key, content, owner, fence, adapter, recovery=True)
        if adapter.reconcile(content, live) not in {"applied", "partial"}:
            raise BoundaryClosed("Recovery preconditions no longer hold")
        self.write(
            key,
            "recovery",
            content,
            owner,
            fence,
            adapter,
            lambda: adapter.recover(content["recovery"], content),
            recovery=True,
        )
        self.write(
            key,
            "recovery_persistence",
            content,
            owner,
            fence,
            adapter,
            lambda: adapter.persist(content, recovering=True),
            recovery=True,
        )
        if adapter.verify_recovery(content) and self.services(
            key, content, owner, fence, adapter, recovery=True
        ):
            return self.record(key, "recovered", {"reason": reason, "recovery_verified": True})
        return self.record(key, "manual_recovery_required", {"reason": "Recovery could not be verified"})
