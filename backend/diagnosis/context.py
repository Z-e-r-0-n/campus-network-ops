"""Build an inspectable evidence packet from normalized, allowlisted fields."""

import math
from datetime import UTC, datetime

from backend.db import Conflict, Missing, canonical, digest
from backend.security.redaction import redact

METRICS = {
    "sent",
    "received",
    "loss_percent",
    "median_rtt_ms",
    "p95_rtt_ms",
    "p99_rtt_ms",
    "rtt_spread_ms",
    "window_start",
    "window_end",
    "windows",
    "tcp_connected",
    "connect_ms",
    "duration_ms",
    "http_status",
    "expected_status",
    "tls_identity_verified",
    "protocol",
    "new_errors",
    "received_packets",
    "errors_per_million_packets",
    "errors_per_second",
    "duration_seconds",
    "memory_percent",
    "cpu_percent",
    "offered_mbps",
    "received_mbps",
}


def fields(record, names):
    return redact({k: record[k] for k in names if k in record})


def fresh(at, maximum=900):
    try:
        return 0 <= (datetime.now(UTC) - datetime.fromisoformat(at)).total_seconds() <= maximum
    except (ValueError, TypeError):
        return False


def packet(tx, subject):
    record = tx.get(subject["kind"], subject["id"])
    device_ids = sorted({record[k] for k in ("source_id", "target_id", "device_id") if record.get(k)})
    if not device_ids:
        raise Conflict("This finding has no identified infrastructure scope")
    devices = [tx.get("device", key) for key in device_ids]
    if any(d.get("status") != "accepted" for d in devices):
        raise Conflict("Review the affected device identities before requesting a diagnosis")
    content = {
        "schema_version": 1,
        "subject": {
            "kind": subject["kind"],
            **fields(
                record,
                [
                    "id",
                    "revision",
                    "title",
                    "status",
                    "rule_id",
                    "policy_id",
                    "evidence_state",
                    "condition_digest",
                ],
            ),
        },
        "devices": [
            fields(d, ["id", "label", "addresses", "platform", "firmware", "role", "status"]) for d in devices
        ],
        "observations": [],
        "policy": None,
        "connections": [],
        "gaps": [],
    }
    edges = tx.c.execute(
        "SELECT * FROM records WHERE kind='edge' AND data->>'status'='accepted' "
        "AND data->>'decision'='include' AND data->>'source_id'=ANY(%s) "
        "AND data->>'target_id'=ANY(%s) ORDER BY id LIMIT 17",
        (device_ids, device_ids),
    ).fetchall()
    content["connections"] = [
        fields(
            tx.unpack(row),
            ["id", "source_id", "target_id", "local_port", "remote_port", "layer", "baseline_id"],
        )
        for row in edges[:16]
    ]
    if len(edges) > 16:
        content["gaps"].append("Only 16 reviewed connections within the affected scope are included")
    content["gaps"].append(
        "Connections outside the affected device scope are not included; absence is not proof of isolation"
    )
    if record.get("policy_id"):
        policy = tx.get("check_policy", record["policy_id"])
        content["policy"] = fields(
            policy,
            [
                "id",
                "revision",
                "check_id",
                "source_id",
                "target_id",
                "target_address",
                "interval_seconds",
                "enabled",
            ],
        )
        content["policy"]["parameters"] = fields(
            policy.get("parameters", {}),
            [
                "samples",
                "max_loss_percent",
                "max_rtt_ms",
                "max_age_seconds",
                "window_seconds",
                "protocol",
                "port",
                "expected_status",
            ],
        )
        rows = tx.c.execute(
            "SELECT * FROM records WHERE kind='check_result' AND data->>'policy_id'=%s ORDER BY created DESC,id DESC LIMIT 13",
            (record["policy_id"],),
        ).fetchall()
        if len(rows) > 12:
            content["gaps"].append("Only the 12 most recent measurement results are included")
        for row in rows[:12]:
            item = tx.unpack(row)
            if item.get("source_id") not in device_ids or item.get("target_id") not in device_ids:
                continue
            observation = fields(
                item,
                [
                    "id",
                    "check_id",
                    "status",
                    "reason",
                    "source_id",
                    "target_id",
                    "observed_at",
                    "window_start",
                    "evidence_ids",
                    "policy_fingerprint",
                ],
            )
            observation["citation"] = "check_result:" + item["id"]
            observation["metrics"] = {
                k: v
                for k, v in fields(item.get("metrics", {}), METRICS).items()
                if isinstance(v, (str, bool, int, float)) or v is None
                if not isinstance(v, float) or math.isfinite(v)
            }
            observation["fresh"] = fresh(
                item.get("observed_at"), policy.get("parameters", {}).get("max_age_seconds", 900)
            )
            content["observations"].append(observation)
    if subject["kind"] == "recommendation":
        rows = tx.c.execute(
            "SELECT * FROM records WHERE kind='configuration_facts' AND data->>'device_id'=%s ORDER BY updated DESC,id DESC LIMIT 4",
            (record["device_id"],),
        ).fetchall()
        from backend.reviews.rules import RULES

        allowed = {key for rule in RULES.values() for key in rule[1]}
        for row in rows:
            item = tx.unpack(row)
            content["observations"].append(
                {
                    **fields(item, ["id", "device_id", "observed_at", "baseline_revision", "evidence_ids"]),
                    "citation": "configuration_facts:" + item["id"],
                    "facts": {
                        k: v for k, v in item.get("facts", {}).items() if k in allowed and isinstance(v, bool)
                    },
                    "fresh": fresh(item.get("observed_at"), 86400),
                }
            )
    if not content["observations"]:
        raise Conflict("Collect source-timestamped measurements or configuration facts first")
    if not any(item["fresh"] for item in content["observations"]):
        raise Conflict("The available evidence is stale; collect current observations first")
    content["gaps"].extend(
        [
            "No physical inspection or independent cause verification is included",
            "No raw configuration, credentials or executable instructions are included",
        ]
    )
    if len(canonical(content).encode()) > 32000:
        raise Conflict("The evidence packet exceeds the diagnosis size limit")
    return content


def still_current(tx, diagnosis):
    try:
        current = packet(tx, diagnosis["subject"])
        return digest(current) == digest(diagnosis["packet"])
    except (Conflict, Missing, ValueError):
        return False
