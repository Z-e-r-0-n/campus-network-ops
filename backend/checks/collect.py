"""Bounded local probe operations. Never run a target-supplied command."""

import re
import socket
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

from backend.checks.evaluate import evaluate, unknown
from backend.checks.policies import approved_context, definition, identity
from backend.checks.probes import local_binding
from backend.checks.services import response
from backend.db import Conflict, digest, now, uid


class MeasurementBusy(Exception):
    pass


@contextmanager
def measurement_slot(store, source_id, target_id, attempt):
    instant = datetime.now(UTC)
    devices = {source_id, target_id}
    with store.tx(write=True) as tx:
        rows = tx.c.execute(
            "SELECT data FROM records WHERE kind='measurement_lease' AND (data->>'until')::timestamptz>%s",
            (instant,),
        ).fetchall()
        if len(rows) >= 20 or any(devices.intersection(row["data"]["device_ids"]) for row in rows):
            raise MeasurementBusy()
        tx.put(
            "measurement_lease",
            {"device_ids": sorted(devices), "until": (instant + timedelta(seconds=120)).isoformat()},
            attempt,
        )
    try:
        yield
    finally:
        with store.tx(write=True) as tx:
            tx.c.execute("DELETE FROM records WHERE kind='measurement_lease' AND id=%s", (attempt,))


def ping(source, target, count=10):
    source, target = str(ip_address(source)), str(ip_address(target))
    if not 1 <= count <= 10:
        raise ValueError("Ordinary ping is limited to ten samples")
    command = ["ping", "-n", "-I", source, "-c", str(count), "-i", "1", "-W", "2", "-w", "20", target]
    try:
        reply = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=23,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"observed_at": now(), "supported": False}
    sent = re.search(r"(\d+) packets transmitted", reply.stdout)
    if not sent:
        return {"observed_at": now(), "supported": False}
    samples = [float(v) for v in re.findall(r"time[=<]([\d.]+) ms", reply.stdout)]
    return {
        "observed_at": now(),
        "sent": int(sent[1]),
        "rtt_ms": samples,
        "source": source,
        "target": target,
        "scope": "controller-origin ICMP",
        "duration_limit_seconds": 20,
    }


def tcp(source, target, port):
    if port not in {22, 53, 80, 443}:
        raise ValueError("This probe only supports configured infrastructure service ports")
    src, dst = ip_address(source), ip_address(target)
    if src.version != dst.version:
        raise ValueError("Source and destination address families differ")
    start = time.monotonic()
    with socket.socket(socket.AF_INET6 if src.version == 6 else socket.AF_INET, socket.SOCK_STREAM) as conn:
        conn.settimeout(3)
        try:
            conn.bind((str(src), 0))
        except OSError:
            return {
                "observed_at": now(),
                "supported": False,
                "source": str(src),
                "target": str(dst),
                "port": port,
                "reason": "source_binding_unavailable",
            }
        try:
            conn.connect((str(dst), port))
            valid = True
        except OSError:
            valid = False
    return {
        "observed_at": now(),
        "tcp_connected": valid,
        # Reaching a socket does not prove that the required application responded.
        "service_response_valid": False if not valid else None,
        "connect_ms": (time.monotonic() - start) * 1000,
        "source": str(src),
        "target": str(dst),
        "port": port,
        "scope": "TCP connection only; application response untested",
    }


def run_check(store, policy_id, run_id=None):
    attempt = uid()
    if run_id:
        with store.tx(write=True) as tx:
            run = tx.get("check_run", run_id)
            if run["policy_id"] != policy_id:
                raise Conflict("The check run belongs to a different policy")
            if run.get("result_id"):
                return tx.get("check_result", run["result_id"])
            policy = tx.get("check_policy", policy_id)
            if run["status"] != "queued":
                return run
            if (
                policy.get("active_run_id") != run_id
                or policy.get("approval_id") != run["approval_id"]
                or datetime.fromisoformat(run["expires_at"]) <= datetime.now(UTC)
            ):
                return tx.put("check_run", {**run, "status": "expired", "finished_at": now()}, run_id)
            tx.put(
                "check_run",
                {
                    **run,
                    "status": "running",
                    "attempt": attempt,
                    "started_at": now(),
                    "lease_until": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                },
                run_id,
            )
    with store.tx() as tx:
        policy = tx.get("check_policy", policy_id)
        source, target = tx.get("device", policy["source_id"]), tx.get("device", policy["target_id"])
        profile = tx.get("probe", source["id"], False)
        normalized = tx.get(
            "measurement",
            digest({"check": policy["check_id"], "source": source["id"], "target": target["id"]}),
            False,
        )
    check_id = policy["check_id"]
    if not policy.get("enabled"):
        evidence, outcome = {"observed_at": None}, unknown("policy_disabled")
    elif source.get("status") != "accepted" or target.get("status") != "accepted":
        evidence, outcome = {"observed_at": None}, unknown("unreviewed_path")
    elif profile and profile.get("verified_local") and check_id in {"H01", "H02", "H03", "H04"}:
        try:
            with store.tx() as tx:
                approved_context(tx, policy)
            if local_binding(profile["source_address"]) != profile.get("binding"):
                raise Conflict("The verified measurement interface changed")
            with measurement_slot(store, source["id"], target["id"], attempt):
                # Recheck after source verification, immediately before target traffic.
                with store.tx() as tx:
                    current = tx.get("check_policy", policy_id)
                    if not current.get("enabled") or current.get("approval_id") != policy.get("approval_id"):
                        raise Conflict("The measurement was paused or changed")
                    approved_context(tx, current)
                src, dst = profile["source_address"], policy["target_address"]
                parameters = policy["parameters"]
                if check_id == "H04":
                    evidence = (
                        tcp(src, dst, parameters["port"])
                        if parameters["protocol"] == "tcp"
                        else response(src, dst, parameters)
                    )
                else:
                    evidence = ping(src, dst, parameters["samples"])
            outcome = evaluate(check_id, evidence, parameters)
        except MeasurementBusy:
            evidence, outcome = {"observed_at": None}, unknown("measurement_budget_busy")
        except (Conflict, ValueError, OSError, subprocess.SubprocessError):
            evidence, outcome = {"observed_at": None}, unknown("probe_or_approval_needs_review")
    elif normalized:
        evidence = normalized["evidence"]
        outcome = evaluate(check_id, evidence, policy.get("parameters"))
    else:
        evidence = {"observed_at": None}
        outcome = unknown("source_capability_or_measurement_unavailable")
    with store.tx(write=True) as tx:
        # A queued activity or an in-flight probe may outlive a policy change.
        current = tx.get("check_policy", policy_id)
        current_source = tx.get("device", source["id"])
        current_target = tx.get("device", target["id"])
        if run_id:
            run = tx.get("check_run", run_id)
            if (
                run.get("attempt") != attempt
                or run["status"] != "running"
                or current.get("active_run_id") != run_id
            ):
                return run
            if datetime.fromisoformat(run["lease_until"]) <= datetime.now(UTC):
                return tx.put("check_run", {**run, "status": "expired", "finished_at": now()}, run_id)
        changed = (
            definition(current) != definition(policy)
            or current.get("enabled") != policy.get("enabled")
            or current.get("approval_id") != policy.get("approval_id")
            or identity(current_source) != identity(source)
            or identity(current_target) != identity(target)
            or tx.get("probe", source["id"], False) != profile
        )
        if changed:
            outcome = unknown("policy_or_path_changed_during_collection")
        elif outcome.get("reason") == "measurement_budget_busy" and run_id:
            # No packets were sent. Retry the skipped slot instead of stacking
            # traffic on a busy source or repeatedly starving a later policy.
            retry_at = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
            tx.put("check_policy", {**current, "next_due": retry_at, "next_eligible_at": retry_at}, policy_id)
        evidence_record = tx.put(
            "evidence",
            {
                "source": source["id"],
                "target": target["id"],
                "collected_at": now(),
                "observed_at": evidence.get("observed_at"),
                "measurement": evidence,
            },
        )
        evidence_ids = [evidence_record["id"]]
        window = None
        if (
            check_id == "H02"
            and not changed
            and (outcome["status"] in {"passed", "failed"} or outcome.get("reason") == "insufficient_samples")
        ):
            window = rolling_window(tx, policy, evidence_record)
            outcome = evaluate(check_id, window, policy.get("parameters"))
            outcome["metrics"].update(
                {
                    "window_start": window["window_start"],
                    "window_end": window["observed_at"],
                    "windows": len(window["evidence_ids"]),
                }
            )
            evidence_ids = window["evidence_ids"]
        record = tx.put(
            "check_result",
            {
                **outcome,
                "check_id": check_id,
                "policy_id": policy_id,
                "policy_fingerprint": digest(
                    {"definition": definition(policy), "approval_id": policy.get("approval_id")}
                ),
                "source_id": source["id"],
                "target_id": target["id"],
                "observed_at": evidence.get("observed_at"),
                "evidence_ids": evidence_ids,
                "window_start": window["window_start"] if window else None,
                "run_id": run_id,
            },
        )
        if run_id:
            tx.put(
                "check_run",
                {**run, "status": "complete", "result_id": record["id"], "finished_at": now()},
                run_id,
            )
        correlate(tx, policy, record)
        tx.audit("checks", "check.finished", record["id"], {"check_id": check_id, "status": record["status"]})
    return record


def rolling_window(tx, policy, evidence):
    measurement = evidence["measurement"]
    observed = datetime.fromisoformat(measurement["observed_at"])
    fingerprint = digest({"definition": definition(policy), "approval_id": policy.get("approval_id")})
    previous = tx.get("check_window", policy["id"], False) or {}
    chunks = previous.get("chunks", []) if previous.get("fingerprint") == fingerprint else []
    if not chunks or observed > datetime.fromisoformat(chunks[-1]["observed_at"]):
        cutoff = observed - timedelta(seconds=policy["parameters"]["window_seconds"])
        chunks = [c for c in chunks if cutoff < datetime.fromisoformat(c["observed_at"]) <= observed]
        # A gap is a broken observation series, not a fresh continuous distribution.
        if (
            chunks
            and (observed - datetime.fromisoformat(chunks[-1]["observed_at"])).total_seconds()
            > policy["interval_seconds"] * 2
        ):
            chunks = []
        chunks.append(
            {
                "observed_at": measurement["observed_at"],
                "sent": measurement["sent"],
                "rtt_ms": measurement["rtt_ms"],
                "evidence_id": evidence["id"],
            }
        )
    chunks = chunks[-100:]  # At most 1,000 RTT samples and 100 evidence references per policy.
    tx.put("check_window", {"fingerprint": fingerprint, "chunks": chunks}, policy["id"])
    return {
        "observed_at": chunks[-1]["observed_at"],
        "window_start": chunks[0]["observed_at"],
        "sent": sum(c["sent"] for c in chunks),
        "rtt_ms": [rtt for c in chunks for rtt in c["rtt_ms"]],
        "evidence_ids": [c["evidence_id"] for c in chunks],
    }


def correlate(tx, policy, check):
    key = digest({"policy_id": policy["id"]})
    streak = tx.get("check_streak", key, False) or {"failed": 0, "passed": 0}
    fingerprint = check.get("policy_fingerprint")
    if streak.get("policy_fingerprint") != fingerprint:
        streak = {"failed": 0, "passed": 0}
    try:
        observed = datetime.fromisoformat(check["observed_at"])
        if observed.tzinfo is None:
            raise ValueError("Source time requires an offset")
        previous = (
            datetime.fromisoformat(streak["last_observed_at"]) if streak.get("last_observed_at") else None
        )
    except (KeyError, TypeError, ValueError):
        observed, previous = None, None
    # New database result IDs are not new source observations. Preserve the last
    # valid streak when a cached or out-of-order poll is re-evaluated.
    if observed and previous and observed <= previous:
        return
    if check["status"] not in {"passed", "failed"}:
        tx.put(
            "check_streak",
            {
                **streak,
                "failed": 0,
                "passed": 0,
                "policy_fingerprint": fingerprint,
                "last_result": check["id"],
            },
            key,
        )
        return
    if observed is None:
        return
    # Overlapping rolling distributions are not independent failure windows.
    if check.get("window_start") and streak.get("window_end"):
        if datetime.fromisoformat(check["window_start"]) <= datetime.fromisoformat(streak["window_end"]):
            return
    # A missed schedule breaks consecutive-window evidence. This tolerance is
    # scheduling continuity, not an assumed network performance threshold.
    continuity = (
        policy.get("parameters", {}).get("window_seconds", 300)
        if check.get("window_start")
        else policy.get("interval_seconds", 300)
    )
    if previous and (observed - previous).total_seconds() > continuity * 2:
        streak = {"failed": 0, "passed": 0}
    state = check["status"]
    streak = {
        "failed": streak.get("failed", 0) + 1 if state == "failed" else 0,
        "passed": streak.get("passed", 0) + 1 if state == "passed" else 0,
        "last_result": check["id"],
        "last_observed_at": check["observed_at"],
        "policy_fingerprint": fingerprint,
        "window_end": check["observed_at"] if check.get("window_start") else None,
    }
    tx.put("check_streak", streak, key)
    incident = tx.get("incident", key, False)
    if streak["failed"] >= 3:
        if not incident or incident["status"] == "resolved":
            previous = incident.get("episodes", []) if incident else []
            if incident:
                previous = [
                    *previous,
                    {"opened_at": incident["opened_at"], "resolved_at": incident.get("resolved_at")},
                ]
            incident = {
                "title": f"{policy['check_id']} failed on the measured path",
                "status": "open",
                "opened_at": now(),
                "episodes": previous,
                "source_id": policy["source_id"],
                "target_id": policy["target_id"],
                "policy_id": policy["id"],
            }
        tx.put(
            "incident",
            {
                **incident,
                "summary": check["summary"],
                "last_result_id": check["id"],
                "evidence_ids": check["evidence_ids"],
            },
            key,
        )
    elif incident and incident["status"] != "resolved" and streak["passed"] >= 3:
        tx.put(
            "incident",
            {
                **incident,
                "status": "resolved",
                "resolved_at": now(),
                "last_result_id": check["id"],
                "resolution": "Three fresh consecutive passing check windows",
            },
            key,
        )
