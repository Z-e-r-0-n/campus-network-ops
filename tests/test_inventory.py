from types import SimpleNamespace

import pytest

from backend.connectors.netbox import NetBox, publish_baseline
from backend.db import Conflict
from backend.discovery.review import (
    BaselineDraft,
    EdgeEdit,
    MergeDevices,
    SplitDevice,
    approve_baseline,
    merge_devices,
    prepare_baseline,
    save_edge,
    split_device,
)
from backend.domain.models import Approval
from backend.workflows.runtime import INVENTORY_KINDS, OBSERVATION_KINDS


def draft(db, *, count=1):
    with db.tx(write=True) as tx:
        connector = tx.put("connector", {"kind": "netbox", "secret_ref": "test", "status": "connected"})
        devices = [
            tx.put(
                "device",
                {
                    "label": f"Example {n}",
                    "status": "draft",
                    "role": "access",
                    "addresses": [f"192.0.2.{n + 1}"],
                },
            )
            for n in range(count)
        ]
        request = BaselineDraft(
            connector_id=connector["id"],
            reason="Reviewed test inventory",
            devices=[
                {
                    "id": d["id"],
                    "revision": d["revision"],
                    "mapping": {"site": 1, "device_type": 2, "role": 3, "status": "active"},
                }
                for d in devices
            ],
        )
        result = prepare_baseline(tx, request, "operator")
    return result, devices, connector


def accept(db, record):
    with db.tx(write=True) as tx:
        return approve_baseline(
            tx, record["id"], Approval(digest=record["digest"], revision=record["revision"]), "operator"
        )


class Publisher:
    def __init__(self):
        self.calls = []

    def publish_device(self, device, mapping, baseline):
        self.calls.append(device["id"])
        return {
            "external_id": len(self.calls),
            "last_updated": "2026-01-01T00:00:00Z",
            "etag": '"version1"',
            "baseline_id": baseline,
        }


def test_discovery_intent_requires_review_and_separate_queue(db):
    baseline, devices, _ = draft(db)
    with db.tx() as tx:
        assert tx.get("device", devices[0]["id"])["status"] == "draft"
        assert not tx.c.execute("SELECT 1 FROM outbox WHERE kind='inventory_publication'").fetchone()
    queued = accept(db, baseline)
    assert queued["status"] == "queued"
    assert set(INVENTORY_KINDS).isdisjoint(OBSERVATION_KINDS)
    with db.tx() as tx:
        assert tx.get("device", devices[0]["id"])["status"] == "draft"
        assert (
            tx.c.execute("SELECT count(*) AS n FROM outbox WHERE kind='inventory_publication'").fetchone()[
                "n"
            ]
            == 1
        )


def test_edited_device_invalidates_inventory_approval(db):
    baseline, devices, _ = draft(db)
    with db.tx(write=True) as tx:
        tx.put("device", {**devices[0], "role": "core"}, devices[0]["id"])
    with pytest.raises(Conflict):
        accept(db, baseline)


def test_conflicting_identity_cannot_be_accepted(db):
    baseline, devices, _ = draft(db)
    with db.tx(write=True) as tx:
        changed = tx.put("device", {**devices[0], "status": "conflict"}, devices[0]["id"])
        content = baseline["content"]
        content["devices"][0]["revision"] = changed["revision"]
        with pytest.raises(Conflict):
            prepare_baseline(
                tx,
                BaselineDraft(
                    connector_id=content["connector_id"],
                    reason="Review conflict",
                    devices=[
                        {
                            "id": changed["id"],
                            "revision": changed["revision"],
                            "mapping": content["devices"][0]["mapping"],
                        }
                    ],
                ),
                "operator",
            )


def test_tampered_executable_inventory_fields_are_rejected(db):
    baseline, _, _ = draft(db)
    with db.tx(write=True) as tx:
        altered = [{**baseline["devices"][0], "mapping": {**baseline["devices"][0]["mapping"], "site": 999}}]
        changed = tx.put("baseline", {**baseline, "devices": altered}, baseline["id"])
    with pytest.raises(Conflict):
        accept(db, changed)


def test_publication_accepts_only_after_every_readback(db, monkeypatch):
    baseline, devices, _ = draft(db, count=2)
    publisher = Publisher()
    monkeypatch.setattr("backend.connectors.netbox.NetBox", lambda *args: publisher)
    settings = SimpleNamespace(secret=lambda reference: "test")
    with pytest.raises(Conflict):
        publish_baseline(db, settings, baseline["id"])
    accept(db, baseline)
    result = publish_baseline(db, settings, baseline["id"])
    assert result["status"] == "accepted"
    with db.tx() as tx:
        assert all(tx.get("device", d["id"])["status"] == "accepted" for d in devices)
    publish_baseline(db, settings, baseline["id"])
    assert len(publisher.calls) == 2


def test_partial_external_writes_never_accept_a_partial_baseline(db, monkeypatch):
    baseline, devices, _ = draft(db, count=2)
    publisher = Publisher()
    original = publisher.publish_device

    def fail_second(device, mapping, key):
        if publisher.calls:
            raise ConnectionError("Unknown outcome from intended inventory")
        return original(device, mapping, key)

    publisher.publish_device = fail_second
    monkeypatch.setattr("backend.connectors.netbox.NetBox", lambda *args: publisher)
    accept(db, baseline)
    result = publish_baseline(db, SimpleNamespace(secret=lambda reference: "test"), baseline["id"])
    assert result["status"] == "publication_failed"
    with db.tx() as tx:
        saved = tx.get("baseline", baseline["id"])
        assert len(saved["published"]) == 1
        assert all(tx.get("device", d["id"])["status"] == "draft" for d in devices)


def test_connection_correction_requires_endpoints_and_physical_ports(db):
    _, devices, _ = draft(db, count=2)
    with pytest.raises(ValueError):
        EdgeEdit(
            source_id=devices[0]["id"],
            target_id=devices[1]["id"],
            layer="confirmed_physical",
            reason="Ports not verified",
        )
    with db.tx(write=True) as tx:
        edge = save_edge(
            tx,
            EdgeEdit(
                source_id=devices[0]["id"],
                target_id=devices[1]["id"],
                layer="logical_route",
                decision="include",
                reason="Reviewed route relationship",
            ),
            "operator",
        )
        assert edge["status"] == "draft"
        assert edge["layer"] == "logical_route"


def test_external_patch_uses_server_enforced_if_match():
    client = NetBox({"endpoint": "http://127.0.0.1/api", "pinned_address": "127.0.0.1"}, "test")
    calls = []
    external = {
        "id": 8,
        "name": "Example",
        "last_updated": "version1",
        "comments": "Campus Ops identity: test-device",
        "site": {"id": 1},
        "device_type": {"id": 2},
        "role": {"id": 3},
        "status": {"value": "active"},
    }

    def request(method, route, body=None, **kwargs):
        calls.append((method, kwargs))
        if method == "PATCH":
            assert kwargs["headers"]["If-Match"] == '"etag1"'
            return external
        return {"data": external, "etag": '"etag1"'}

    client.http.request = request
    mapping = {
        "site": 1,
        "device_type": 2,
        "role": 3,
        "status": "active",
        "external_id": 8,
        "expected_last_updated": "version1",
        "expected_etag": '"etag1"',
    }
    client.publish_device({"id": "test-device", "label": "Example"}, mapping, "baseline")
    assert [c[0] for c in calls] == ["GET", "PATCH", "GET"]
    calls.clear()
    with pytest.raises(Conflict):
        client.publish_device(
            {"id": "test-device", "label": "Example"}, {**mapping, "expected_etag": '"stale"'}, "baseline"
        )
    assert [c[0] for c in calls] == ["GET"]


def test_merge_retains_alias_history_and_demotes_connections(db):
    _, devices, _ = draft(db, count=2)
    with db.tx(write=True) as tx:
        edge = save_edge(
            tx,
            EdgeEdit(
                source_id=devices[0]["id"],
                target_id=devices[1]["id"],
                layer="advertised_neighbor",
                decision="include",
                reason="Prior assumption",
            ),
            "operator",
        )
        merged = merge_devices(
            tx,
            MergeDevices(
                primary={"id": devices[0]["id"], "revision": devices[0]["revision"]},
                aliases=[{"id": devices[1]["id"], "revision": devices[1]["revision"]}],
                reason="Compared the device identity from both aliases",
            ),
            "operator",
        )
        assert merged["addresses"] == ["192.0.2.1", "192.0.2.2"]
        assert merged["status"] == "draft"
        alias = tx.get("device", devices[1]["id"])
        assert alias["status"] == "merged" and alias["retired_addresses"] == ["192.0.2.2"]
        assert tx.get("edge", edge["id"])["decision"] == "unresolved"


def test_split_does_not_invent_new_device_identity(db):
    with db.tx(write=True) as tx:
        old = tx.put(
            "device",
            {
                "label": "Combined identity",
                "addresses": ["192.0.2.1", "192.0.2.2"],
                "serial": "SERIAL-A",
                "platform": "ios",
                "role": "core",
                "status": "draft",
            },
        )
        child = split_device(
            tx,
            SplitDevice(
                device={"id": old["id"], "revision": old["revision"]},
                addresses=["192.0.2.2"],
                label="Unresolved other device",
                reason="Separate chassis seen at this address",
            ),
            "operator",
        )
        assert child["role"] == "unknown" and child["observed_at"] is None
        assert "serial" not in child and "platform" not in child
        assert tx.get("device", old["id"])["addresses"] == ["192.0.2.1"]


def test_duplicate_management_address_blocks_baseline_even_after_manual_edit(db):
    baseline, devices, _ = draft(db)
    with db.tx(write=True) as tx:
        tx.put(
            "device",
            {
                "label": "Unresolved duplicate",
                "role": "unknown",
                "status": "draft",
                "addresses": devices[0]["addresses"],
            },
        )
        with pytest.raises(Conflict):
            prepare_baseline(
                tx,
                BaselineDraft(
                    connector_id=baseline["connector_id"],
                    reason="Still ambiguous",
                    devices=[
                        {
                            "id": devices[0]["id"],
                            "revision": devices[0]["revision"],
                            "mapping": baseline["devices"][0]["mapping"],
                        }
                    ],
                ),
                "operator",
            )
