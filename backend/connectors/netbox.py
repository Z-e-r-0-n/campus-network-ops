"""Publish only a reviewed inventory diff. Raw observations are never auto-published."""

from backend.connectors.http import PinnedHTTP
from backend.db import Conflict, digest, now
from backend.discovery.review import validate_revisions


def authorization(token):
    return {"Authorization": ("Bearer " if token.startswith("nbt_") else "Token ") + token}


class NetBox:
    def __init__(self, connector, token):
        self.http = PinnedHTTP(connector, authorization(token))

    def options(self):
        result = {}
        for kind in ["sites", "manufacturers", "device-types", "device-roles"]:
            data = self.http.request("GET", f"/dcim/{kind}/?limit=500")
            result[kind] = [
                {
                    "id": row["id"],
                    "name": row.get("display", row.get("name", "")),
                    "manufacturer_id": (row.get("manufacturer") or {}).get("id"),
                }
                for row in data.get("results", [])
            ]
        return result

    def publish_device(self, device, mapping, baseline_id):
        from urllib.parse import quote

        marker = f"Campus Ops identity: {device['id']}"
        payload = {
            "name": device["label"],
            "site": mapping["site"],
            "device_type": mapping["device_type"],
            "role": mapping["role"],
            "status": mapping["status"],
            "comments": marker,
        }
        if mapping.get("external_id"):
            response = self.http.request(
                "GET", f"/dcim/devices/{mapping['external_id']}/", with_metadata=True
            )
            existing = response["data"]
            if (
                not mapping.get("expected_etag")
                or response["etag"] != mapping["expected_etag"]
                or existing.get("last_updated") != mapping.get("expected_last_updated")
            ):
                raise Conflict("The intended inventory changed after review")
            if marker not in existing.get("comments", ""):
                raise Conflict("This external record is not bound to the reviewed device identity")
            payload["comments"] = existing["comments"]
            result = self.http.request(
                "PATCH",
                f"/dcim/devices/{existing['id']}/",
                payload,
                headers={"If-Match": mapping["expected_etag"]},
            )
        else:
            matches = self.http.request(
                "GET", "/dcim/devices/?name=" + quote(device["label"], safe="") + "&limit=100"
            )
            owned = [row for row in matches.get("results", []) if marker in row.get("comments", "")]
            if len(owned) > 1:
                raise Conflict("Multiple external records claim this identity")
            if owned:
                result = owned[0]
            elif matches.get("count", 0):
                raise Conflict("An existing external device has this name; reconcile it before publishing")
            else:
                result = self.http.request("POST", "/dcim/devices/", payload)
        response = self.http.request("GET", f"/dcim/devices/{result['id']}/", with_metadata=True)
        readback = response["data"]
        if (
            readback.get("status", {}).get("value") != mapping["status"]
            or readback.get("name") != device["label"]
            or any(readback.get(key, {}).get("id") != mapping[key] for key in ["site", "device_type", "role"])
        ):
            raise Conflict("Published inventory differs from the reviewed mapping")
        return {
            "external_id": readback["id"],
            "last_updated": readback["last_updated"],
            "etag": response["etag"],
            "baseline_id": baseline_id,
        }

    def create_object(self, request):
        import re
        import unicodedata
        from urllib.parse import quote

        kind = request["kind"]
        if kind not in {"sites", "manufacturers", "device-types", "device-roles"}:
            raise ValueError("Unsupported intended inventory object")
        name = request["name"]
        slug = re.sub(
            r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        ).strip("-")[:80]
        slug = slug or "item-" + digest(name)[:12]
        data = {"model" if kind == "device-types" else "name": name, "slug": slug}
        if kind == "device-types":
            data["manufacturer"] = request["manufacturer_id"]
        if kind == "device-roles":
            data["color"] = "28765e"
        query = "/dcim/" + kind + "/?slug=" + quote(slug, safe="")
        rows = self.http.request("GET", query).get("results", [])
        if rows:
            row = rows[0]
            if len(rows) != 1 or row.get("model" if kind == "device-types" else "name") != name:
                raise Conflict("An inventory object already uses this slug")
            if kind == "device-types" and row.get("manufacturer", {}).get("id") != data["manufacturer"]:
                raise Conflict("Device type belongs to a different manufacturer")
        else:
            row = self.http.request("POST", "/dcim/" + kind + "/", data)
        return {"id": row["id"], "name": row.get("display", name), "kind": kind}

    def publish_connection(self, edge, devices):
        from urllib.parse import quote

        if edge["layer"] != "confirmed_physical":
            return {"representation": "reviewed_overlay", "layer": edge["layer"]}
        interfaces = []
        for endpoint, field in [("source_id", "local_port"), ("target_id", "remote_port")]:
            device_id = devices[edge[endpoint]]["external_id"]
            rows = self.http.request(
                "GET", f"/dcim/interfaces/?device_id={device_id}&name=" + quote(edge[field], safe="")
            ).get("results", [])
            if len(rows) > 1:
                raise Conflict("Multiple external interfaces match a reviewed endpoint")
            if rows:
                interface = rows[0]
            else:
                # No inferred speed/media type. The reviewed port name is the only supplied fact.
                interface = self.http.request(
                    "POST", "/dcim/interfaces/", {"device": device_id, "name": edge[field], "type": "other"}
                )
            interfaces.append(interface)
        marker = "Campus link: " + edge["id"]
        a, b = interfaces
        bound = [item.get("cable") for item in interfaces]
        if any(bound):
            if not all(bound) or bound[0]["id"] != bound[1]["id"]:
                raise Conflict("A reviewed interface is already attached to another cable")
            cable = self.http.request("GET", f"/dcim/cables/{bound[0]['id']}/")
            if cable.get("description") != marker:
                raise Conflict("Existing physical connection requires external reconciliation")
        else:
            cable = self.http.request(
                "POST",
                "/dcim/cables/",
                {
                    "a_terminations": [{"object_type": "dcim.interface", "object_id": a["id"]}],
                    "b_terminations": [{"object_type": "dcim.interface", "object_id": b["id"]}],
                    "status": "connected",
                    "description": marker,
                },
            )
        readback = self.http.request("GET", f"/dcim/cables/{cable['id']}/")

        def termination_ids(side):
            return {
                row.get("object_id")
                for row in readback.get(side + "_terminations", [])
                if row.get("object_type") == "dcim.interface"
            }

        if termination_ids("a") != {a["id"]} or termination_ids("b") != {b["id"]}:
            raise Conflict("Physical connection readback differs from the reviewed endpoints")
        return {"representation": "netbox_cable", "external_id": cable["id"]}


def publish_baseline(store, settings, baseline_id):
    with store.tx(write=True) as tx:
        baseline = tx.get("baseline", baseline_id)
        if baseline["status"] == "accepted":
            return baseline
        if baseline["status"] not in {"queued", "publishing"} or not baseline.get("approved_by"):
            raise Conflict("Inventory publication needs an approved review")
        connector = tx.get("connector", baseline["connector_id"])
        tx.put("baseline", {**baseline, "status": "publishing"}, baseline_id)
    results = dict(baseline.get("published", {}))
    links = dict(baseline.get("published_connections", {}))
    try:
        if connector["kind"] != "netbox":
            raise ValueError("Accepted inventory requires the intended-inventory connector")
        client = NetBox(connector, settings.secret(connector["secret_ref"]))
        with store.tx() as tx:
            validate_revisions(tx, baseline)
        for item in baseline["devices"]:
            with store.tx() as tx:
                validate_revisions(tx, baseline)
                device = tx.get("device", item["id"])
            mapping = dict(item["mapping"])
            # An acknowledged earlier write can be re-read using its resulting ETag.
            # An ambiguous write is never overwritten with an old ETag.
            if item["id"] in results and mapping.get("external_id"):
                mapping.update(
                    expected_etag=results[item["id"]].get("etag"),
                    expected_last_updated=results[item["id"]]["last_updated"],
                )
            results[item["id"]] = client.publish_device(device, mapping, baseline_id)
            with store.tx(write=True) as tx:
                current = tx.get("baseline", baseline_id)
                tx.put("baseline", {**current, "published": results}, baseline_id)
        for item in baseline["edges"]:
            with store.tx() as tx:
                validate_revisions(tx, baseline)
                edge = tx.get("edge", item["id"])
            links[item["id"]] = client.publish_connection(edge, results)
            with store.tx(write=True) as tx:
                current = tx.get("baseline", baseline_id)
                tx.put("baseline", {**current, "published_connections": links}, baseline_id)
        with store.tx(write=True) as tx:
            validate_revisions(tx, baseline)
            for item in baseline["devices"]:
                device = tx.get("device", item["id"])
                tx.put(
                    "device",
                    {
                        **device,
                        "status": "accepted",
                        "baseline_id": baseline_id,
                        "netbox": results[item["id"]],
                    },
                    device["id"],
                    item["revision"],
                )
            for item in baseline["edges"]:
                edge = tx.get("edge", item["id"])
                tx.put(
                    "edge",
                    {
                        **edge,
                        "status": "accepted",
                        "baseline_id": baseline_id,
                        "publication": links[item["id"]],
                    },
                    edge["id"],
                    item["revision"],
                )
            current = tx.get("baseline", baseline_id)
            baseline = tx.put(
                "baseline", {**current, "status": "accepted", "accepted_at": now()}, baseline_id
            )
            tx.audit(
                "inventory",
                "baseline.accepted",
                baseline_id,
                {"devices": len(results), "connections": len(links)},
            )
        return baseline
    except Exception:
        with store.tx(write=True) as tx:
            current = tx.get("baseline", baseline_id)
            tx.put(
                "baseline",
                {
                    **current,
                    "status": "publication_failed",
                    "published": results,
                    "published_connections": links,
                    "detail": "Publication or revision verification failed. Partial external records are retained for reconciliation.",
                },
                baseline_id,
            )
            tx.audit(
                "inventory", "baseline.publication_failed", baseline_id, {"published_count": len(results)}
            )
        return {"id": baseline_id, "status": "publication_failed"}
