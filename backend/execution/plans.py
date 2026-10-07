"""Plan compilation is deterministic. Free-form model output cannot become commands."""

import re
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from typing import Literal

from pydantic import Field, model_validator

from backend.db import Conflict, digest, now, uid
from backend.domain.models import Model


class RepairRequest(Model):
    device_id: str
    runbook_id: Literal["RB01", "RB02", "RB03", "RB04", "RB05", "RB06", "RB07"]
    parameters: dict
    evidence_id: str


class ServiceSettings(Model):
    service: Literal["ntp", "syslog"]
    servers: list[str] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def addresses(self):
        self.servers = sorted(set(str(ip_address(v)) for v in self.servers))
        return self


class Enrollment(Model):
    username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{1,30}$")
    public_key: str = Field(max_length=2048)
    role: Literal["read", "execute"]

    @model_validator(mode="after")
    def key(self):
        from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
        from cryptography.hazmat.primitives.serialization import load_ssh_public_key

        try:
            key = load_ssh_public_key(self.public_key.encode())
        except (ValueError, TypeError):
            raise ValueError("A valid OpenSSH public key is required") from None
        if not isinstance(key, (rsa.RSAPublicKey, ed25519.Ed25519PublicKey)):
            raise ValueError("Use a supported RSA or Ed25519 public key")
        if isinstance(key, rsa.RSAPublicKey) and key.key_size < 2048:
            raise ValueError("RSA key must have at least 2048 bits")
        self.public_key = " ".join(self.public_key.split()[:2])
        return self


class DhcpSettings(Model):
    vlans: list[int] = Field(min_length=1, max_length=16)
    trusted_ports: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def bounds(self):
        if any(v < 1 or v > 4094 for v in self.vlans):
            raise ValueError("VLAN must be between 1 and 4094")
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9/.-]{0,48}", p) for p in self.trusted_ports):
            raise ValueError("Invalid interface name")
        return self


class TrunkSettings(Model):
    interface: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9/.-]{0,48}$")
    vlans: list[int] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounds(self):
        if any(v < 1 or v > 4094 for v in self.vlans):
            raise ValueError("Invalid VLAN")
        return self


class RouteSettings(Model):
    prefix: str
    next_hop: str

    @model_validator(mode="after")
    def route(self):
        net, hop = ip_network(self.prefix, strict=True), ip_address(self.next_hop)
        if net.version != hop.version:
            raise ValueError("Route and next hop must have the same address family")
        self.prefix, self.next_hop = str(net), str(hop)
        return self


class SaveSettings(Model):
    expected_running_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_startup_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class RecoverySettings(Model):
    original_execution_id: str


PARAMETERS = {
    "RB01": Enrollment,
    "RB02": ServiceSettings,
    "RB03": SaveSettings,
    "RB04": DhcpSettings,
    "RB05": TrunkSettings,
    "RB06": RouteSettings,
    "RB07": RecoverySettings,
}
ACTION = {
    "RB01": "access.enroll",
    "RB02": "service.replace_servers",
    "RB03": "configuration.save",
    "RB04": "dhcp.enforce",
    "RB05": "trunk.set_vlans",
    "RB06": "route.replace",
    "RB07": "configuration.recover_fields",
}


def compile_plan(tx, request, actor):
    params = PARAMETERS[request.runbook_id](**request.parameters).model_dump()
    device = tx.get("device", request.device_id)
    evidence = tx.get("configuration_snapshot", request.evidence_id)
    if evidence["device_id"] != device["id"]:
        raise ValueError("Evidence belongs to a different device")
    blockers = []
    if device.get("status") != "accepted":
        blockers.append("Accept the device identity and intended role first")
    qualified = tx.get("qualification", device["id"], False) or {}
    if qualified.get("firmware") != device.get("firmware") or ACTION[request.runbook_id] not in qualified.get(
        "actions", []
    ):
        blockers.append("This action has not been qualified for the exact device firmware")
    if not evidence.get("recovery_access_verified"):
        blockers.append("A usable recovery path must be verified")
    try:
        if (
            not 0
            <= (datetime.now(UTC) - datetime.fromisoformat(evidence["observed_at"])).total_seconds()
            <= 900
        ):
            blockers.append("Collect fresh configuration evidence")
    except (KeyError, ValueError, TypeError):
        blockers.append("Configuration source time is unavailable")
    action = {"type": ACTION[request.runbook_id], "parameters": params}
    before = evidence.get("managed_fields", {})
    if request.runbook_id == "RB02":
        field = params["service"] + "_servers"
        if field not in before:
            blockers.append("Current service destinations were not collected")
        change = {"field": field, "before": before.get(field), "after": params["servers"]}
    else:
        change = {"before": before, "after": params}
    if not evidence.get("service_verification_policy"):
        blockers.append("Service verification policy must be configured")
    content = {
        "schema_version": 1,
        "runbook_id": request.runbook_id,
        "runbook_version": "1",
        "device_id": device["id"],
        "device_revision": device["revision"],
        "device_identity": {k: device.get(k) for k in ["serial", "platform", "firmware", "addresses"]},
        "snapshot_id": request.evidence_id,
        "snapshot_digest": evidence["configuration_digest"],
        "adapter": qualified.get("adapter"),
        "adapter_version": qualified.get("adapter_version"),
        "actions": [action],
        "change": change,
        "service_verification_policy": evidence.get("service_verification_policy"),
        "recovery": {"type": "restore_managed_fields", "before": before, "preserve_unrelated": True},
        "limits": {"max_targets": 1, "max_duration_seconds": 300, "max_attempts": 1},
        "observation_seconds": 60,
    }
    plan = tx.put(
        "plan",
        {
            "content": content,
            "digest": digest(content),
            "status": "draft" if blockers else "awaiting_approval",
            "blockers": blockers,
            "created_by": actor,
        },
    )
    tx.audit(actor, "plan.prepared", plan["id"], {"digest": plan["digest"], "blockers": blockers})
    return plan


def approve(tx, plan_id, approval, actor, mutations_enabled):
    plan = tx.get("plan", plan_id)
    if plan["digest"] != approval.digest or plan["revision"] != approval.revision:
        raise Conflict("The plan changed. Review its latest version.")
    if not mutations_enabled:
        raise Conflict("Execution is not commissioned for this installation")
    if plan["status"] != "awaiting_approval" or plan["blockers"]:
        raise Conflict("The plan is not ready for approval")
    if digest(plan["content"]) != plan["digest"]:
        raise Conflict("Plan integrity verification failed")
    device = tx.get("device", plan["content"]["device_id"])
    if device["revision"] != plan["content"]["device_revision"]:
        raise Conflict("Device state changed. Prepare a new plan.")
    pause = tx.get("system", "execution_pause", False)
    if pause and pause.get("active"):
        raise Conflict("Execution is paused")
    execution_id = uid()
    approved = tx.put(
        "approval",
        {
            "plan_id": plan_id,
            "digest": plan["digest"],
            "actor": actor,
            "approved_at": now(),
            "expires": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            "revoked": False,
        },
    )
    execution = tx.put(
        "execution",
        {
            "plan_id": plan_id,
            "approval_id": approved["id"],
            "status": "queued",
            "digest": plan["digest"],
            "steps": [],
            "stop_requested": False,
        },
        execution_id,
    )
    tx.put("plan", {**plan, "status": "approved", "execution_id": execution_id}, plan_id)
    tx.enqueue("execution", {"execution_id": execution_id}, execution_id)
    tx.audit(actor, "plan.approved", plan_id, {"digest": plan["digest"], "execution_id": execution_id})
    return execution
