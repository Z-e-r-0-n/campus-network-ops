"""Reviewable measurement budgets and durable, non-overlapping check dispatch."""

from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Literal

from pydantic import Field, model_validator

from backend.db import Conflict, digest, now, uid
from backend.domain.models import Model


class LocalParameters(Model):
    samples: int = Field(default=10, ge=1, le=10)
    max_age_seconds: int = Field(default=120, ge=30, le=900)
    max_loss_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    max_rtt_ms: float | None = Field(default=None, gt=0, le=60000, allow_inf_nan=False)
    window_seconds: int = Field(default=3600, ge=600, le=86400)
    protocol: Literal["tcp", "https", "http", "ssh"] = "tcp"
    port: Literal[22, 53, 80, 443] = 443
    hostname: str = Field(default="", max_length=253, pattern=r"^[A-Za-z0-9.-]*$")
    path: str = Field(default="/", max_length=512, pattern=r"^/[^\s\x00-\x1f\x7f]*$")
    expected_status: int = Field(default=200, ge=100, le=599)

    @model_validator(mode="after")
    def application(self):
        if self.protocol in {"https", "http"} and not self.hostname:
            raise ValueError("Enter the expected service hostname")
        if self.protocol == "https" and self.port != 443:
            raise ValueError("HTTPS checks use port 443")
        if self.protocol == "http" and self.port != 80:
            raise ValueError("HTTP checks use port 80")
        if self.protocol == "ssh" and self.port != 22:
            raise ValueError("SSH banner checks use port 22")
        return self


def identity(device):
    return {k: device.get(k) for k in ("id", "addresses", "status", "platform", "serial", "role")}


def definition(policy):
    return {
        k: policy.get(k)
        for k in ("check_id", "source_id", "target_id", "target_address", "interval_seconds", "parameters")
    }


def context(tx, policy):
    source = tx.get("device", policy["source_id"])
    target = tx.get("device", policy["target_id"])
    probe = tx.get("probe", source["id"], False)
    if source.get("status") != "accepted" or target.get("status") != "accepted":
        raise Conflict("Accept both source and destination identities in inventory first")
    if not probe or not probe.get("verified_local") or not probe.get("binding"):
        raise Conflict("Verify the measurement source on the observation worker first")
    if probe["source_address"] not in source.get("addresses", []):
        raise Conflict("The verified source address is no longer assigned to this identity")
    address = policy.get("target_address")
    if address not in target.get("addresses", []):
        raise Conflict("Choose an explicit destination address from the accepted identity")
    src, dst = ip_address(probe["source_address"]), ip_address(address)
    if src.version != dst.version or dst.is_multicast or dst.is_unspecified:
        raise Conflict("Use unicast source and destination addresses of the same address family")
    return {
        "source": identity(source),
        "target": identity(target),
        "probe": {k: probe.get(k) for k in ("id", "revision", "source_address", "binding")},
    }


def prepare(tx, policy_id, actor):
    policy = tx.get("check_policy", policy_id)
    if policy["check_id"] not in {"H01", "H02", "H03", "H04"}:
        raise Conflict("This check's collector is not yet available for scheduled activation")
    parameters = LocalParameters.model_validate(policy.get("parameters", {}))
    if policy["check_id"] in {"H01", "H03"} and parameters.max_loss_percent is None:
        raise ValueError("Set the maximum accepted packet loss before review")
    if policy["check_id"] == "H02":
        if parameters.max_rtt_ms is None:
            raise ValueError("Set the maximum accepted 95th percentile RTT before review")
        if parameters.window_seconds < 100 / parameters.samples * policy["interval_seconds"]:
            raise ValueError("The measurement window must allow at least 100 received samples")
    reviewed = {**definition(policy), "parameters": parameters.model_dump()}
    snapshot = context(tx, reviewed)
    content = {
        "policy": reviewed,
        "context": snapshot,
        "budget": {
            "scope": "controller-origin measurements",
            "packets_per_run": parameters.samples if policy["check_id"] != "H04" else None,
            "connections_per_run": 1 if policy["check_id"] == "H04" else 0,
            "interval_seconds": policy["interval_seconds"],
            "max_duration_seconds": 23,
            "application_response": parameters.protocol if policy["check_id"] == "H04" else None,
        },
    }
    review = tx.put(
        "check_review",
        {
            "policy_id": policy_id,
            "policy_revision": policy["revision"],
            "content": content,
            "digest": digest(content),
            "status": "awaiting_review",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        },
    )
    tx.audit(actor, "check.review_prepared", review["id"], {"policy_id": policy_id})
    return review


def approve(tx, review_id, approval, actor):
    review = tx.get("check_review", review_id)
    if review["status"] != "awaiting_review" or review["revision"] != approval.revision:
        raise Conflict("This measurement review changed; prepare a new review")
    if review["digest"] != approval.digest or digest(review["content"]) != approval.digest:
        raise Conflict("The approval must match the exact measurement review")
    if datetime.fromisoformat(review["expires_at"]) <= datetime.now(UTC):
        raise Conflict("This review expired; prepare a fresh measurement review")
    policy = tx.get("check_policy", review["policy_id"])
    if policy["revision"] != review["policy_revision"]:
        raise Conflict("The check changed after review")
    if context(tx, review["content"]["policy"]) != review["content"]["context"]:
        raise Conflict("The source or destination changed after review")
    result = tx.put(
        "check_policy",
        {
            **policy,
            **review["content"]["policy"],
            "enabled": True,
            "approval_id": review_id,
            "hold_reason": None,
            "next_due": max(now(), policy.get("next_eligible_at") or now()),
        },
        policy["id"],
    )
    tx.put(
        "check_review",
        {**review, "status": "approved", "approved_by": actor, "approved_at": now()},
        review_id,
    )
    tx.audit(actor, "check.approved", policy["id"], {"review_id": review_id, "digest": approval.digest})
    return result


def approved_context(tx, policy):
    review = tx.get("check_review", policy.get("approval_id", ""), False)
    if not review or review["status"] != "approved":
        raise Conflict("The measurement policy needs approval")
    content = review["content"]
    if digest(content) != review["digest"] or content["policy"] != definition(policy):
        raise Conflict("The measurement definition changed; review it again")
    if content["context"] != context(tx, policy):
        raise Conflict("The accepted path or probe changed; review the measurement again")
    return review


def enqueue(tx, policy, actor="scheduler", instant=None):
    instant = instant or datetime.now(UTC)
    if not policy.get("enabled"):
        raise Conflict("Enable this check through a measurement review first")
    approved_context(tx, policy)
    active = tx.get("check_run", policy.get("active_run_id", ""), False)
    if active and active["status"] in {"queued", "running"}:
        deadline = active.get("lease_until") if active["status"] == "running" else active["expires_at"]
        if datetime.fromisoformat(deadline) > instant:
            return active
        tx.put("check_run", {**active, "status": "expired", "finished_at": instant.isoformat()}, active["id"])
    if policy.get("next_eligible_at") and datetime.fromisoformat(policy["next_eligible_at"]) > instant:
        raise Conflict(
            "The next measurement is not due yet; the approved interval also applies to manual runs"
        )
    run = tx.put(
        "check_run",
        {
            "policy_id": policy["id"],
            "approval_id": policy["approval_id"],
            "status": "queued",
            "requested_by": actor,
            "expires_at": (
                instant + timedelta(seconds=max(120, min(policy["interval_seconds"], 600)))
            ).isoformat(),
        },
        uid(),
    )
    tx.enqueue("check", {"policy_id": policy["id"], "run_id": run["id"]}, run["id"])
    next_due = (instant + timedelta(seconds=policy["interval_seconds"])).isoformat()
    tx.put(
        "check_policy",
        {**policy, "active_run_id": run["id"], "next_eligible_at": next_due, "next_due": next_due},
        policy["id"],
    )
    tx.audit(actor, "check.queued", run["id"], {"policy_id": policy["id"]})
    return run
