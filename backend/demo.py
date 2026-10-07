"""Explicit demonstration fixtures. Never imported by installation or discovery."""

import re

from backend.discovery.review import BaselineDraft, prepare_baseline

DEMO_USERNAME = "demo"


def permits_demo_action(method, path):
    if path == "/api/v1/session" and method in {"POST", "DELETE"}:
        return True
    if method == "POST" and path in {
        "/api/v1/devices",
        "/api/v1/connections",
        "/api/v1/baseline-drafts",
        "/api/v1/identity-merges",
        "/api/v1/identity-splits",
    }:
        return True
    if method == "PATCH" and re.fullmatch(r"/api/v1/(devices|connections)/[^/]+", path):
        return True
    return method == "POST" and bool(re.fullmatch(r"/api/v1/devices/[^/]+/disposition", path))


def seed_demo(db):
    """Write into the launcher's private schema, with no observations or measurements."""
    with db.tx(write=True) as tx:
        connector = tx.put(
            "connector",
            {
                "name": "Sample intended inventory",
                "kind": "netbox",
                "endpoint": "http://127.0.0.1:9/api",
                "pinned_address": "127.0.0.1",
                "secret_ref": "demo_has_no_connector_credentials",
                "status": "not_validated",
                "source_timezone": "UTC",
                "source_timezone_verified": False,
            },
            "demo-inventory",
        )
        tx.put(
            "inventory_options",
            {
                "connector_id": connector["id"],
                "collected_at": None,
                "source": "demonstration_fixture",
                "options": {
                    "sites": [{"id": 1, "name": "Example site"}],
                    "manufacturers": [{"id": 1, "name": "Example manufacturer"}],
                    "device-types": [{"id": 1, "model": "Example model", "name": "Example model"}],
                    "device-roles": [{"id": 1, "name": "Example infrastructure"}],
                },
            },
            connector["id"],
        )
        devices = []
        for index, (name, role) in enumerate(
            [
                ("Example firewall", "firewall"),
                ("Example core", "core"),
                ("Example access switch A", "access"),
                ("Example access switch B", "access"),
                ("Example monitoring server", "server"),
            ],
            1,
        ):
            devices.append(
                tx.put(
                    "device",
                    {
                        "label": name,
                        "role": role,
                        "addresses": [f"192.0.2.{index}"],
                        "location": "Example site",
                        "status": "draft",
                        "access": "not_configured",
                        "source": "demonstration_fixture",
                        "observed_at": None,
                        "platform": "unknown",
                        "serial": None,
                        "firmware": None,
                        "reason": "Illustrative sample; not discovered from any network.",
                    },
                    f"demo-device-{index}",
                )
            )
        edges = []
        for index, (source, target, layer, decision) in enumerate(
            [
                (0, 1, "logical_route", "include"),
                (1, 2, "advertised_neighbor", "include"),
                (1, 3, "advertised_neighbor", "unresolved"),
                (4, 1, "service_dependency", "include"),
            ],
            1,
        ):
            edges.append(
                tx.put(
                    "edge",
                    {
                        "source_id": devices[source]["id"],
                        "target_id": devices[target]["id"],
                        "remote_address": devices[target]["addresses"][0],
                        "layer": layer,
                        "decision": decision,
                        "status": "draft",
                        "local_port": "",
                        "remote_port": "",
                        "source": "demonstration_fixture",
                        "observed_at": None,
                        "reason": "Illustrative relationship; no physical cable has been verified.",
                    },
                    f"demo-edge-{index}",
                )
            )
        prepare_baseline(
            tx,
            BaselineDraft.model_validate(
                {
                    "connector_id": connector["id"],
                    "devices": [
                        {
                            "id": d["id"],
                            "revision": d["revision"],
                            "mapping": {
                                "site": 1,
                                "device_type": 1,
                                "role": 1,
                                "status": "planned",
                            },
                        }
                        for d in devices
                    ],
                    "edges": [
                        {"id": e["id"], "revision": e["revision"]}
                        for e in edges
                        if e["decision"] == "include"
                    ],
                    "reason": "Sample review: inspect five devices and three relationships before publication.",
                }
            ),
            "demo-fixture",
        )
        tx.audit(
            "demo-fixture",
            "demo.initialized",
            "demonstration",
            {
                "sample_data": True,
                "network_operations": False,
            },
        )
