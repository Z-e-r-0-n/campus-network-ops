from backend.db import now
from backend.discovery.engine import Discovery
from backend.domain.models import DiscoveryRequest


def start(db, max_nodes=500):
    spec = DiscoveryRequest(
        seeds=["192.0.2.1"], connector_id="test", scope={"prefixes": ["192.0.2.0/24"], "max_nodes": max_nodes}
    )
    with db.tx(write=True) as tx:
        return Discovery(db).create(tx, spec.model_dump(), "test")


def test_cycles_scope_and_no_duplicate_reads(db):
    calls = []

    def read(address):
        calls.append(address)
        return {
            "label": address,
            "serial": address,
            "source": "test",
            "observed_at": now(),
            "neighbors": [{"address": "192.0.2.1"}, {"address": "192.0.2.2"}, {"address": "198.51.100.1"}],
        }

    job = start(db)
    completed = Discovery(db).run(job["id"], read)
    assert calls == ["192.0.2.1", "192.0.2.2"]
    assert completed["status"] == "partial"
    assert any(g["address"] == "198.51.100.1" for g in completed["gaps"])


def test_budget_and_restart(db):
    job = start(db, max_nodes=1)
    calls = []

    def read(address):
        calls.append(address)
        return {"neighbors": [{"address": "192.0.2.2"}], "source": "test"}

    first = Discovery(db).run(job["id"], read)
    assert first["status"] == "partial" and first["frontier"]
    Discovery(db).run(job["id"], read)
    assert len(calls) == 1


def test_identity_conflict_preserves_observation(db):
    with db.tx(write=True) as tx:
        a = Discovery.identity(tx, "192.0.2.1", {"serial": "first", "platform": "ios"}, "e1")
        b = Discovery.identity(tx, "192.0.2.1", {"serial": "second", "platform": "ios"}, "e2")
        assert a["id"] != b["id"]
        assert b["status"] == "conflict"
        assert tx.get("device", a["id"])["serial"] == "first"


def test_serial_alias_merge(db):
    with db.tx(write=True) as tx:
        a = Discovery.identity(tx, "192.0.2.1", {"serial": "same", "platform": "ios"}, "e1")
        b = Discovery.identity(tx, "192.0.2.2", {"serial": "same", "platform": "ios"}, "e2")
        assert a["id"] == b["id"]
        assert b["addresses"] == ["192.0.2.1", "192.0.2.2"]


def test_cancellation_during_read_cannot_resurrect_run(db):
    job = start(db)

    def read(address):
        with db.tx(write=True) as tx:
            current = tx.get("discovery", job["id"])
            tx.put("discovery", {**current, "status": "cancelled"}, job["id"])
        return {"serial": "late", "neighbors": [{"address": "192.0.2.2"}]}

    result = Discovery(db).run(job["id"], read)
    assert result["status"] == "cancelled"
    with db.tx() as tx:
        assert tx.list("device") == []


def test_conflict_marks_both_identities_and_deduplicates_repeated_read(db):
    with db.tx(write=True) as tx:
        original = Discovery.identity(tx, "192.0.2.1", {"serial": "first", "platform": "ios"}, "e1")
        tx.put("device", {**original, "status": "accepted"}, original["id"])
        conflict = Discovery.identity(tx, "192.0.2.1", {"serial": "second", "platform": "ios"}, "e2")
        again = Discovery.identity(tx, "192.0.2.1", {"serial": "second", "platform": "ios"}, "e3")
        assert conflict["id"] == again["id"]
        assert tx.get("device", original["id"])["status"] == "conflict"
        assert len(tx.list("device")) == 2


def test_missing_or_monitoring_fields_do_not_overwrite_direct_identity(db):
    with db.tx(write=True) as tx:
        item = Discovery.identity(
            tx, "192.0.2.1", {"source": "ssh", "platform": "ios", "firmware": "fresh"}, "e1"
        )
        Discovery.identity(
            tx, "192.0.2.1", {"source": "librenms", "platform": "unknown", "firmware": "old"}, "e2"
        )
        after = tx.get("device", item["id"])
        assert after["firmware"] == "fresh" and after["platform"] == "ios"


def test_discovery_preserves_operator_connection_review(db):
    def read(address):
        return {
            "source": "test",
            "neighbors": [{"address": "192.0.2.2", "local_port": "port-1", "remote_port": "port-2"}]
            if address.endswith(".1")
            else [],
        }

    first = start(db)
    Discovery(db).run(first["id"], read)
    with db.tx(write=True) as tx:
        edge = tx.list("edge")[0]
        tx.put(
            "edge",
            {
                **edge,
                "decision": "rejected",
                "reviewed_by": "operator",
                "reason": "Indirect neighbor through unmanaged segment",
            },
            edge["id"],
        )
    second = start(db)
    Discovery(db).run(second["id"], read)
    with db.tx() as tx:
        reviewed = tx.get("edge", edge["id"])
        assert reviewed["decision"] == "rejected"
        assert reviewed["latest_observation"]["remote_address"] == "192.0.2.2"


def test_excluded_seed_is_skipped_before_network_access(db):
    with db.tx(write=True) as tx:
        excluded = tx.put(
            "device",
            {
                "addresses": ["192.0.2.1"],
                "status": "excluded",
                "label": "Out of scope",
                "reason": "Operator excluded this identity",
            },
        )
    job = start(db)

    def must_not_read(address):
        raise AssertionError("An excluded seed must not be contacted")

    result = Discovery(db).run(job["id"], must_not_read)
    assert result["status"] == "complete"
    assert result["nodes"] == []
    assert result["exclusions"][0]["device_id"] == excluded["id"]
    with db.tx() as tx:
        assert len(tx.list("device")) == 1
        assert not tx.list("evidence")


def test_excluded_neighbor_is_observed_but_not_traversed(db):
    with db.tx(write=True) as tx:
        tx.put("device", {"addresses": ["192.0.2.2"], "status": "excluded", "label": "Excluded neighbor"})
    calls = []

    def read(address):
        calls.append(address)
        return {"neighbors": [{"address": "192.0.2.2"}], "source": "test", "observed_at": now()}

    job = start(db)
    result = Discovery(db).run(job["id"], read)
    assert calls == ["192.0.2.1"]
    assert result["exclusions"][0]["address"] == "192.0.2.2"
    with db.tx() as tx:
        assert len(tx.list("device")) == 2
        assert len(tx.list("edge")) == 1  # Preserve the advertisement as evidence.


def test_exclusion_during_collection_cannot_restore_or_expand_identity(db):
    with db.tx(write=True) as tx:
        device = tx.put("device", {"addresses": ["192.0.2.1"], "status": "draft", "label": "Pending"})
    calls = []

    def read(address):
        calls.append(address)
        with db.tx(write=True) as tx:
            tx.put("device", {**device, "status": "excluded"}, device["id"])
        return {"neighbors": [{"address": "192.0.2.2"}], "serial": "newly-read"}

    job = start(db)
    result = Discovery(db).run(job["id"], read)
    assert calls == ["192.0.2.1"]
    assert not result["nodes"]
    with db.tx() as tx:
        assert len(tx.list("device")) == 1
        assert tx.get("device", device["id"])["status"] == "excluded"
        assert not tx.list("edge")


def test_excluded_serial_alias_cannot_reenter_inventory(db):
    with db.tx(write=True) as tx:
        excluded = tx.put(
            "device",
            {"addresses": ["192.0.2.2"], "status": "excluded", "serial": "retired-serial", "platform": "ios"},
        )
        result = Discovery.identity(tx, "192.0.2.3", {"serial": "retired-serial", "platform": "ios"}, "e1")
        assert result["id"] == excluded["id"]
        assert result["status"] == "excluded"
        assert "192.0.2.3" in result["addresses"]
        assert len(tx.list("device")) == 1
