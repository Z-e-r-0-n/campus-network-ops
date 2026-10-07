from datetime import datetime, timedelta, timezone

import pytest

from backend.checks.evaluate import counters, evaluate
from backend.db import now
from backend.reviews.rules import RULES, evaluate_rule, run_review


@pytest.mark.parametrize("number", range(1, 31))
def test_missing_evidence_never_passes(number):
    assert evaluate(f"H{number:02}", {"observed_at": now()})["status"] == "unknown"


def test_loss_p95_stale_and_resets():
    sample = {"observed_at": now(), "sent": 10, "rtt_ms": [1, 2, 3, 4, 5]}
    assert evaluate("H01", sample)["status"] == "failed"
    assert evaluate("H02", sample)["reason"] == "insufficient_samples"
    sample["observed_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert evaluate("H01", sample)["reason"] == "stale_input"
    previous = {
        "at": "2026-09-29T10:00:00+00:00",
        "epoch": "a",
        "interface_id": "p1",
        "packets": 100,
        "errors": 10,
    }
    current = {**previous, "at": "2026-09-29T10:05:00+00:00", "packets": 200, "errors": 11}
    assert counters(previous, current)["errors_per_million_packets"] == 10000
    assert counters(previous, {**current, "epoch": "b"}) is None
    assert counters(previous, {**current, "errors": 0}) is None


@pytest.mark.parametrize("rule_id", list(RULES))
def test_rule_prerequisites(rule_id):
    _, required, field, _ = RULES[rule_id]
    facts = {key: True for key in required}
    assert evaluate_rule(rule_id, facts)["status"] == "clear"
    facts[field] = False
    assert evaluate_rule(rule_id, facts)["status"] == "finding"
    assert evaluate_rule(rule_id, {})["status"] == "unknown"


def test_review_dedup_and_resolution(db):
    facts = {"dhcp_path_verified": True, "dhcp_enforcement_matches_policy": False}
    with db.tx(write=True) as tx:
        source = tx.put("configuration_facts", {"device_id": "switch", "observed_at": now(), "facts": facts})
    run_review(db)
    run_review(db)
    with db.tx(write=True) as tx:
        assert len(tx.list("recommendation")) == 1
        tx.put(
            "configuration_facts",
            {**source, "facts": {**facts, "dhcp_enforcement_matches_policy": True}},
            source["id"],
        )
    run_review(db)
    with db.tx() as tx:
        assert tx.list("recommendation")[0]["status"] == "resolved"
