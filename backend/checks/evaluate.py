"""Pure measurement evaluation. Missing or incomparable evidence never passes."""

import math
from datetime import UTC, datetime
from statistics import median


def result(status, summary, metrics=None, reason=None):
    return {"status": status, "summary": summary, "metrics": metrics or {}, "reason": reason}


def unknown(reason="missing_evidence"):
    return result(
        "unknown", "This check needs usable evidence before it can reach a conclusion.", reason=reason
    )


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def compare(value, maximum, unit, title):
    if not finite(value) or not finite(maximum):
        return unknown()
    return result(
        "failed" if value > maximum else "passed",
        f"{title}: {value:g} {unit}; limit {maximum:g} {unit}.",
        {"value": value, "limit": maximum, "unit": unit},
    )


def counters(previous, current):
    required = ["at", "packets", "errors", "epoch", "interface_id"]
    if any(k not in previous or k not in current for k in required):
        return None
    if previous["epoch"] != current["epoch"] or previous["interface_id"] != current["interface_id"]:
        return None
    try:
        duration = (
            datetime.fromisoformat(current["at"]) - datetime.fromisoformat(previous["at"])
        ).total_seconds()
        packets, errors = current["packets"] - previous["packets"], current["errors"] - previous["errors"]
        if duration <= 0 or packets <= 0 or errors < 0:
            return None
        return {
            "duration_seconds": duration,
            "received_packets": packets,
            "new_errors": errors,
            "errors_per_million_packets": errors * 1_000_000 / packets,
            "errors_per_second": errors / duration,
        }
    except (TypeError, ValueError):
        return None


def assertion(evidence, field, title):
    value = evidence.get(field)
    if not isinstance(value, bool):
        return unknown()
    return result("passed" if value else "failed", title + (" passed." if value else " failed."))


def evaluate(check_id, evidence, policy=None, instant=None):
    policy = policy or {}
    instant = instant or datetime.now(UTC)
    try:
        observed = datetime.fromisoformat(evidence["observed_at"])
        age = (instant - observed).total_seconds()
    except (KeyError, ValueError, TypeError):
        return unknown("missing_source_time")
    if not 0 <= age <= policy.get("max_age_seconds", 900):
        return unknown("stale_input")
    if evidence.get("supported") is False:
        return unknown("unsupported_source")
    if (
        check_id == "H04"
        and evidence.get("tcp_connected") is True
        and evidence.get("service_response_valid") is None
    ):
        return result(
            "unknown",
            "The TCP connection succeeded; an application response was not checked.",
            {"tcp_connected": True, "connect_ms": evidence.get("connect_ms")},
            "application_response_unmeasured",
        )
    if check_id == "H04":
        outcome = assertion(evidence, "service_response_valid", "Expected service response")
        outcome["metrics"] = {
            k: evidence[k]
            for k in (
                "tcp_connected",
                "connect_ms",
                "duration_ms",
                "http_status",
                "expected_status",
                "tls_identity_verified",
                "protocol",
            )
            if k in evidence
        }
        outcome["reason"] = evidence.get("reason", outcome.get("reason"))
        return outcome
    if check_id in {"H01", "H02", "H03"}:
        samples = evidence.get("rtt_ms", [])
        sent = evidence.get("sent")
        if not isinstance(sent, int) or sent < 1 or len(samples) > sent:
            return unknown("invalid_sample_count")
        valid = [v for v in samples if finite(v) and v >= 0]
        if len(valid) != len(samples):
            return unknown("invalid_samples")
        loss = 100 * (sent - len(valid)) / sent
        metrics = {
            "sent": sent,
            "received": len(valid),
            "loss_percent": loss,
            "median_rtt_ms": median(valid) if valid else None,
        }
        if check_id == "H03":
            outcome = compare(loss, policy.get("max_loss_percent", 2), "%", "Packet loss")
        elif check_id == "H02":
            if len(valid) < 100:
                return result(
                    "unknown",
                    "Collect at least 100 valid samples for the delay distribution.",
                    metrics,
                    "insufficient_samples",
                )
            p95 = sorted(valid)[math.ceil(0.95 * len(valid)) - 1]
            metrics.update(
                {
                    "p95_rtt_ms": p95,
                    "p99_rtt_ms": sorted(valid)[math.ceil(0.99 * len(valid)) - 1],
                    "rtt_spread_ms": max(valid) - min(valid),
                }
            )
            outcome = compare(p95, policy.get("max_rtt_ms", 50), "ms", "95th percentile RTT")
        else:
            outcome = compare(
                loss, policy.get("max_loss_percent", 2), "%", "Source-specific reachability loss"
            )
        outcome["metrics"].update(metrics)
        return outcome
    if check_id in {"H05", "H27"}:
        delta = counters(evidence.get("previous", {}), evidence.get("current", {}))
        if not delta:
            return unknown("incomparable_counter_interval")
        if check_id == "H27" and (
            delta["received_packets"] < policy.get("minimum_packets", 10000)
            or delta["duration_seconds"] < policy.get("minimum_seconds", 1800)
        ):
            return result(
                "unknown",
                "More traffic and observation time are needed to verify the link.",
                delta,
                "insufficient_traffic_window",
            )
        outcome = compare(
            delta["errors_per_million_packets"],
            policy.get("max_errors_per_million", 0),
            "errors/M packets",
            "Link errors",
        )
        outcome["metrics"] = delta
        return outcome
    if check_id == "H06":
        sensors = evidence.get("optics")
        if not sensors:
            return unknown("optics_unavailable")
        for sensor in sensors:
            if not all(finite(sensor.get(k)) for k in ["value", "low_alarm", "high_alarm"]):
                return unknown("vendor_limits_missing")
        bad = [s["name"] for s in sensors if not s["low_alarm"] <= s["value"] <= s["high_alarm"]]
        return result(
            "failed" if bad else "passed",
            "Optical alarms: " + (", ".join(bad) if bad else "none"),
            {"sensors": sensors},
        )
    if check_id == "H08":
        values = [
            compare(
                evidence.get("utilization_percent"),
                policy.get("max_utilization_percent", 85),
                "%",
                "Utilization",
            ),
            compare(evidence.get("new_discards"), policy.get("max_discards", 0), "packets", "Discard growth"),
        ]
        return result(
            "failed"
            if any(v["status"] == "failed" for v in values)
            else "unknown"
            if any(v["status"] == "unknown" for v in values)
            else "passed",
            "Utilization and discards evaluated separately; averages cannot exclude microbursts.",
            {"components": values},
        )
    if check_id == "H11":
        traces = evidence.get("traces")
        if not traces or len(traces) < 2 or not evidence.get("route_corroborated"):
            return unknown("route_corroboration_required")
        repeated = all(len([h for h in trace if h]) != len(set(h for h in trace if h)) for trace in traces)
        return result(
            "failed" if repeated else "passed",
            "Repeated route hops are corroborated." if repeated else "No corroborated repeated hops.",
        )
    if check_id == "H12":
        if not all(k in evidence for k in ["mac_move_excess", "broadcast_excess", "stp_change_excess"]):
            return unknown()
        signals = sum(
            evidence[k] is True for k in ["mac_move_excess", "broadcast_excess", "stp_change_excess"]
        )
        return result(
            "failed" if signals >= 2 else "passed",
            f"{signals} correlated Layer-2 loop indicators; inspect physical paths before action.",
        )
    if check_id == "H20":
        if not finite(evidence.get("memory_percent")) or not finite(evidence.get("cpu_percent")):
            return unknown()
        pressure = evidence["memory_percent"] > policy.get("max_memory_percent", 90) or evidence[
            "cpu_percent"
        ] > policy.get("max_cpu_percent", 85)
        return result(
            "failed" if pressure else "passed",
            "Resource headroom is low; this alone does not prove a leak or forwarding failure."
            if pressure
            else "Resource use is within the accepted limits.",
            {k: evidence[k] for k in ["memory_percent", "cpu_percent"]},
        )
    if check_id == "H21":
        offset = evidence.get("clock_offset_ms")
        return compare(
            abs(offset) if finite(offset) else None,
            policy.get("max_clock_offset_ms", 1000),
            "ms",
            "Clock offset",
        )
    if check_id == "H24":
        if not evidence.get("load_authorized") or not finite(evidence.get("received_mbps")):
            return unknown("load_measurement_unavailable")
        offered, received = evidence.get("offered_mbps"), evidence["received_mbps"]
        if not finite(offered) or offered <= 0:
            return unknown()
        return result(
            "failed" if received < offered * policy.get("minimum_delivery_ratio", 0.8) else "passed",
            "Delivery at the approved offered load; this is not maximum path capacity.",
            {"offered_mbps": offered, "received_mbps": received},
        )
    if check_id == "H25":
        if not evidence.get("load_authorized"):
            return unknown("load_measurement_unavailable")
        jitter = compare(
            evidence.get("receiver_jitter_ms"), policy.get("max_jitter_ms", 10), "ms", "Receiver UDP jitter"
        )
        loss = compare(evidence.get("loss_percent"), policy.get("max_loss_percent", 2), "%", "UDP loss")
        return result(
            "failed"
            if "failed" in {jitter["status"], loss["status"]}
            else "passed"
            if jitter["status"] == loss["status"] == "passed"
            else "unknown",
            "Receiver-reported UDP jitter and loss.",
            {"jitter": jitter, "loss": loss},
        )
    if check_id == "H26":
        return (
            compare(
                evidence.get("rtt_increase_ms"),
                policy.get("max_rtt_increase_ms", 20),
                "ms",
                "RTT increase under approved load",
            )
            if evidence.get("load_authorized")
            else unknown("policy_blocked")
        )
    if check_id == "H28":
        return compare(
            evidence.get("source_age_seconds"),
            policy.get("max_poll_age_seconds", 900),
            "seconds",
            "Monitoring observation age",
        )
    fields = {
        "H04": ("service_response_valid", "Service connection and response"),
        "H07": ("negotiation_and_flaps_valid", "Link negotiation and flap limits"),
        "H09": ("required_mtu_verified", "Required path packet size"),
        "H10": ("required_lag_members_active", "Required aggregation members"),
        "H13": ("dns_expected_answer", "DNS answer and transport"),
        "H14": ("dhcp_service_and_capacity_valid", "DHCP service and pool capacity"),
        "H15": ("dhcp_relay_delivery_verified", "DHCP delivery through the relay"),
        "H16": ("only_authorized_dhcp_observed", "Observed DHCP server policy"),
        "H17": ("gateway_identity_matches", "Gateway identity"),
        "H18": ("infrastructure_addresses_unique", "Infrastructure address identity"),
        "H19": ("firewall_forwarded_path_verified", "Firewall forwarded path"),
        "H22": ("config_matches_accepted_and_saved", "Accepted and saved configuration"),
        "H23": ("observed_paths_match_intent", "Observed route paths"),
        "H29": ("backup_and_access_verified", "Backup and fresh access readiness"),
        "H30": ("action_service_bundle_verified", "Post-change service checks"),
    }
    if check_id not in fields:
        raise ValueError("Unknown health check")
    field, title = fields[check_id]
    return assertion(evidence, field, title)
