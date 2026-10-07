"""Operator-reviewed inventory. No network identity or accepted intent is seeded."""

from typing import Literal

from pydantic import Field, model_validator

from backend.db import Conflict, digest, now
from backend.domain.models import Model


class RecordRevision(Model):
    id: str
    revision: int = Field(ge=1)


class NetBoxMapping(Model):
    site: int = Field(ge=1)
    device_type: int = Field(ge=1)
    role: int = Field(ge=1)
    status: Literal["active", "offline", "planned", "staged", "inventory", "decommissioning"]
    external_id: int | None = Field(default=None, ge=1)
    expected_last_updated: str | None = None
    expected_etag: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def conditional_update(self):
        if self.external_id and not (self.expected_last_updated and self.expected_etag):
            raise ValueError("Existing inventory needs a reviewed revision and ETag")
        return self


class ReviewedDevice(RecordRevision):
    mapping: NetBoxMapping


class BaselineDraft(Model):
    connector_id: str
    devices: list[ReviewedDevice] = Field(min_length=1, max_length=500)
    edges: list[RecordRevision] = Field(default_factory=list, max_length=1000)
    reason: str = Field(min_length=3, max_length=500)


class EdgeEdit(Model):
    source_id: str
    target_id: str
    local_port: str = Field(default="", max_length=100)
    remote_port: str = Field(default="", max_length=100)
    layer: Literal[
        "advertised_neighbor", "logical_route", "service_dependency", "confirmed_physical", "proposed"
    ]
    decision: Literal["include", "unresolved", "rejected"] = "unresolved"
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def endpoints(self):
        if self.source_id == self.target_id:
            raise ValueError("A connection needs two different devices")
        if self.layer == "confirmed_physical" and not (self.local_port and self.remote_port):
            raise ValueError("A confirmed physical link needs both verified port names")
        return self


class InventoryObject(Model):
    kind: Literal["sites", "manufacturers", "device-types", "device-roles"]
    name: str = Field(min_length=1, max_length=100)
    manufacturer_id: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def manufacturer_required(self):
        if self.kind == "device-types" and self.manufacturer_id is None:
            raise ValueError("Choose the manufacturer for this device type")
        return self


class MergeDevices(Model):
    primary: RecordRevision
    aliases: list[RecordRevision] = Field(min_length=1, max_length=16)
    reason: str = Field(min_length=10, max_length=500)


class SplitDevice(Model):
    device: RecordRevision
    addresses: list[str] = Field(min_length=1, max_length=31)
    label: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=10, max_length=500)


class IdentityDisposition(Model):
    status: Literal["draft", "excluded"]
    reason: str = Field(min_length=3, max_length=500)


def merge_devices(tx, request, actor):
    selections = [request.primary, *request.aliases]
    if len({item.id for item in selections}) != len(selections):
        raise ValueError("Choose different device records to merge")
    records = []
    for item in selections:
        record = tx.get("device", item.id)
        if record["revision"] != item.revision or record["status"] in {"merged", "excluded"}:
            raise Conflict("A selected identity changed or is no longer active")
        if record.get("netbox"):
            raise Conflict("Published identities require external reconciliation before merging")
        records.append(record)
    primary = records[0]
    addresses = sorted({address for record in records for address in record["addresses"]})
    if len(addresses) > 32:
        raise ValueError("Merged identity exceeds the address limit")
    # Conflicting serials are retained for review, not silently discarded as aliases.
    serials = {record.get("serial") for record in records if record.get("serial")}
    if len(serials) > 1:
        raise Conflict("Serial identities disagree; correct or split the observations before merging")
    primary = tx.put(
        "device",
        {
            **primary,
            "addresses": addresses,
            "status": "draft",
            "access": "not_configured",
            "merged_from": list(
                dict.fromkeys(primary.get("merged_from", []) + [r["id"] for r in records[1:]])
            ),
            "identity_review_reason": request.reason,
        },
        primary["id"],
        request.primary.revision,
    )
    for alias in records[1:]:
        tx.put(
            "device",
            {
                **alias,
                "status": "merged",
                "merged_into": primary["id"],
                "retired_addresses": alias["addresses"],
                "addresses": [],
            },
            alias["id"],
        )
        for row in tx.c.execute(
            "SELECT * FROM records WHERE kind='edge' AND (data->>'source_id'=%s OR data->>'target_id'=%s)",
            (alias["id"], alias["id"]),
        ).fetchall():
            edge = tx.unpack(row)
            for field in ("source_id", "target_id"):
                if edge.get(field) == alias["id"]:
                    edge[field] = primary["id"]
            tx.put("edge", {**edge, "status": "draft", "decision": "unresolved"}, edge["id"])
    tx.audit(
        actor,
        "device.aliases_merged",
        primary["id"],
        {"aliases": [r["id"] for r in records[1:]], "reason": request.reason},
    )
    return primary


def split_device(tx, request, actor):
    from ipaddress import ip_address

    addresses = sorted({str(ip_address(address)) for address in request.addresses})
    device = tx.get("device", request.device.id)
    if device["revision"] != request.device.revision or device["status"] in {"merged", "excluded"}:
        raise Conflict("Device identity changed before the split")
    if device.get("netbox"):
        raise Conflict("Published identities require external reconciliation before splitting")
    if not set(addresses) < set(device["addresses"]):
        raise ValueError("Move some addresses and keep at least one on the original device")
    child = tx.put(
        "device",
        {
            "label": request.label,
            "addresses": addresses,
            "role": "unknown",
            "status": "draft",
            "source": "administrator",
            "observed_at": None,
            "access": "not_configured",
            "split_from": device["id"],
            "identity_review_reason": request.reason,
        },
    )
    tx.put(
        "device",
        {
            **device,
            "addresses": sorted(set(device["addresses"]) - set(addresses)),
            "status": "draft",
            "access": "not_configured",
        },
        device["id"],
    )
    # A split cannot decide which physical links belong to the new chassis.
    for row in tx.c.execute(
        "SELECT * FROM records WHERE kind='edge' AND (data->>'source_id'=%s OR data->>'target_id'=%s)",
        (device["id"], device["id"]),
    ).fetchall():
        edge = tx.unpack(row)
        tx.put("edge", {**edge, "status": "draft", "decision": "unresolved"}, edge["id"])
    tx.audit(
        actor, "device.identity_split", device["id"], {"new_device_id": child["id"], "reason": request.reason}
    )
    return child


def prepare_baseline(tx, request, actor):
    connector = tx.get("connector", request.connector_id)
    if connector["kind"] != "netbox":
        raise ValueError("Choose the intended-inventory connection")
    if len({item.id for item in request.devices}) != len(request.devices):
        raise ValueError("Select each device only once")
    if len({item.id for item in request.edges}) != len(request.edges):
        raise ValueError("Select each connection only once")
    items = []
    for item in request.devices:
        device = tx.get("device", item.id)
        if device["revision"] != item.revision or device["status"] in {"conflict", "merged", "excluded"}:
            raise Conflict("A device changed or has an unresolved identity")
        if device.get("role", "unknown") == "unknown":
            raise Conflict("Review the intended role of every selected device first")
        overlap = tx.c.execute(
            "SELECT 1 FROM records WHERE kind='device' AND id<>%s "
            "AND data->>'status' NOT IN ('merged','excluded') AND data->'addresses' ?| %s LIMIT 1",
            (device["id"], device["addresses"]),
        ).fetchone()
        if overlap:
            raise Conflict("A management address still belongs to multiple device records")
        mapping = item.mapping.model_dump(exclude_none=True)
        if (
            device.get("netbox", {}).get("external_id")
            and mapping.get("external_id") != device["netbox"]["external_id"]
        ):
            raise Conflict("The mapping must retain this device's existing external identity")
        items.append(
            {
                "id": item.id,
                "revision": item.revision,
                "mapping": mapping,
                "reviewed": {
                    key: device.get(key)
                    for key in ["label", "role", "location", "addresses", "serial", "platform", "firmware"]
                },
            }
        )
    edges = []
    device_ids = {item.id for item in request.devices}
    for item in request.edges:
        edge = tx.get("edge", item.id)
        if edge["revision"] != item.revision or edge.get("decision") != "include":
            raise Conflict("Review each selected connection before publication")
        if edge["source_id"] not in device_ids or edge.get("target_id") not in device_ids:
            raise Conflict("Include both endpoints of each reviewed connection")
        edges.append({"id": item.id, "revision": item.revision, "reviewed": edge})
    content = {
        "connector_id": connector["id"],
        "connector_revision": connector["revision"],
        "devices": items,
        "edges": edges,
        "reason": request.reason,
    }
    baseline = tx.put(
        "baseline",
        {
            **content,
            "content": content,
            "digest": digest(content),
            "status": "awaiting_review",
            "created_by": actor,
        },
    )
    tx.audit(actor, "baseline.prepared", baseline["id"], {"digest": baseline["digest"]})
    return baseline


def approve_baseline(tx, key, approval, actor):
    baseline = tx.get("baseline", key)
    if (
        baseline["status"] not in {"awaiting_review", "publication_failed"}
        or baseline["revision"] != approval.revision
        or baseline["digest"] != approval.digest
        or digest(baseline["content"]) != approval.digest
    ):
        raise Conflict("Inventory review changed. Refresh and review it again.")
    validate_revisions(tx, baseline)
    result = tx.put(
        "baseline", {**baseline, "status": "queued", "approved_by": actor, "approved_at": now()}, key
    )
    tx.enqueue("inventory_publication", {"baseline_id": key})
    tx.audit(actor, "baseline.approved", key, {"digest": approval.digest})
    return result


def validate_revisions(tx, baseline):
    if (
        digest(baseline["content"]) != baseline["digest"]
        or {key: baseline.get(key) for key in baseline["content"]} != baseline["content"]
    ):
        raise Conflict("Inventory review integrity changed")
    if tx.get("connector", baseline["connector_id"])["revision"] != baseline["connector_revision"]:
        raise Conflict("Inventory connection changed after review")
    for kind, collection in [("device", "devices"), ("edge", "edges")]:
        for item in baseline[collection]:
            if tx.get(kind, item["id"])["revision"] != item["revision"]:
                raise Conflict("An inventory item changed after review")


def save_edge(tx, body, actor, key=None, expected=None):
    for device_id in (body.source_id, body.target_id):
        device = tx.get("device", device_id)
        if device["status"] in {"conflict", "merged", "excluded"}:
            raise Conflict("Resolve endpoint identity before reviewing this connection")
    old = tx.get("edge", key) if key else {}
    result = tx.put(
        "edge",
        {
            **old,
            **body.model_dump(),
            "status": "draft",
            "reviewed_by": actor,
            "reviewed_at": now(),
            "review_source": "administrator",
        },
        key,
        expected,
    )
    tx.audit(actor, "connection.reviewed", result["id"], {"reason": body.reason, "decision": body.decision})
    return result
