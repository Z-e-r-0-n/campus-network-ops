"""Rules consume sourced, normalized facts and explicit accepted policy."""

from datetime import UTC, datetime

from backend.db import digest, now

RULES = {
    "C01": (
        "Authorized DHCP paths",
        ["dhcp_path_verified", "dhcp_enforcement_matches_policy"],
        "dhcp_enforcement_matches_policy",
        "RB04",
    ),
    "C02": (
        "DHCP relay and snooping compatibility",
        ["relay_policy_verified", "snooping_exceptions_valid"],
        "snooping_exceptions_valid",
        "RB04",
    ),
    "C03": (
        "Loop-prevention coverage",
        ["port_roles_verified", "stp_matches_intent"],
        "stp_matches_intent",
        None,
    ),
    "C04": (
        "VLAN and trunk consistency",
        ["peer_config_verified", "vlan_intent_verified", "trunks_match_intent"],
        "trunks_match_intent",
        "RB05",
    ),
    "C05": (
        "Link configuration",
        ["peer_config_verified", "link_settings_match_intent"],
        "link_settings_match_intent",
        None,
    ),
    "C06": (
        "Unprotected paths",
        ["physical_paths_reviewed", "required_redundancy_present"],
        "required_redundancy_present",
        None,
    ),
    "C07": (
        "Routing and gateway behavior",
        ["route_intent_verified", "return_path_verified", "routes_match_intent"],
        "routes_match_intent",
        "RB06",
    ),
    "C08": (
        "Address and subnet consistency",
        ["address_plan_reviewed", "addresses_match_intent"],
        "addresses_match_intent",
        None,
    ),
    "C09": (
        "Time and event reporting",
        ["time_log_policy_verified", "ntp_syslog_delivery_verified"],
        "ntp_syslog_delivery_verified",
        "RB02",
    ),
    "C10": (
        "Management boundaries",
        ["management_methods_reviewed", "management_scope_matches_intent"],
        "management_scope_matches_intent",
        None,
    ),
    "C11": (
        "Key and account maintenance",
        ["access_policy_verified", "dedicated_key_access_current"],
        "dedicated_key_access_current",
        "RB01",
    ),
    "C12": (
        "Saved configuration and recovery",
        ["backup_capability_verified", "running_startup_match"],
        "running_startup_match",
        "RB03",
    ),
    "C13": (
        "Resource and capacity margin",
        ["capacity_history_representative", "capacity_margin_adequate"],
        "capacity_margin_adequate",
        None,
    ),
    "C14": (
        "Monitoring and test coverage",
        ["expected_coverage_reviewed", "required_coverage_present"],
        "required_coverage_present",
        None,
    ),
    "C15": (
        "Configuration drift",
        ["baseline_reviewed", "config_matches_baseline"],
        "config_matches_baseline",
        "RB07",
    ),
    "C16": (
        "Firewall and service dependencies",
        ["firewall_intent_verified", "firewall_matches_service_intent"],
        "firewall_matches_service_intent",
        None,
    ),
    "C17": (
        "IPv6 control policy",
        ["ipv6_policy_verified", "ipv6_controls_match_policy"],
        "ipv6_controls_match_policy",
        None,
    ),
}


def evaluate_rule(rule_id, facts):
    title, required, field, runbook = RULES[rule_id]
    missing = [key for key in required if key not in facts or not isinstance(facts[key], bool)]
    missing += [key for key in required if key != field and facts.get(key) is not True]
    if missing:
        return {"status": "unknown", "title": title, "missing": sorted(set(missing)), "runbook": runbook}
    return {
        "status": "clear" if facts[field] else "finding",
        "title": title,
        "missing": [],
        "runbook": runbook,
    }


def run_review(store, actor="scheduler"):
    instant = datetime.now(UTC)
    with store.tx(write=True) as tx:
        review = tx.put("review", {"status": "running", "started_at": now()})
        observations = tx.list("configuration_facts", limit=1000)
        counts = {"finding": 0, "clear": 0, "unknown": 0}
        for source in observations:
            try:
                fresh = (
                    0 <= (instant - datetime.fromisoformat(source["observed_at"])).total_seconds() <= 86400
                )
            except (KeyError, ValueError, TypeError):
                fresh = False
            for rule_id in RULES:
                item = evaluate_rule(rule_id, source.get("facts", {}) if fresh else {})
                counts[item["status"]] += 1
                key = digest({"rule": rule_id, "device": source["device_id"]})
                old = tx.get("recommendation", key, False)
                if item["status"] != "finding":
                    if old:
                        tx.put(
                            "recommendation",
                            {
                                **old,
                                "evidence_state": item["status"],
                                "status": "resolved" if item["status"] == "clear" else old["status"],
                                "missing": item["missing"],
                                "last_reviewed_at": now(),
                            },
                            key,
                        )
                    continue
                condition = digest(
                    {
                        "rule": rule_id,
                        "facts": {k: source["facts"].get(k) for k in RULES[rule_id][1]},
                        "baseline_revision": source.get("baseline_revision"),
                    }
                )
                status = "open"
                if (
                    old
                    and old.get("condition_digest") == condition
                    and old["status"] in {"deferred", "excepted", "awaiting_approval", "executing"}
                ):
                    until = old.get("until")
                    if not until or datetime.fromisoformat(until) > instant:
                        status = old["status"]
                tx.put(
                    "recommendation",
                    {
                        **(old or {}),
                        **item,
                        "rule_id": rule_id,
                        "device_id": source["device_id"],
                        "status": status,
                        "evidence_state": "finding",
                        "condition_digest": condition,
                        "evidence_ids": source.get("evidence_ids", []),
                        "last_reviewed_at": now(),
                        "summary": "Observed configuration differs from the reviewed policy. Inspect the supporting facts before preparing a change.",
                    },
                    key,
                )
        review = tx.put(
            "review",
            {
                **review,
                "status": "complete",
                "finished_at": now(),
                "counts": counts,
                "sources": len(observations),
            },
            review["id"],
        )
        tx.audit(actor, "configuration_review.finished", review["id"], counts)
        return review
