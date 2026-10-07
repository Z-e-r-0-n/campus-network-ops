"""Observation-worker source verification; no target traffic is sent during registration."""

import json
import os
import socket
import subprocess
from pathlib import Path

from backend.checks.policies import identity
from backend.db import digest, now


def local_binding(address):
    reply = subprocess.run(
        ["ip", "-j", "address", "show"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )
    candidates = []
    for interface in json.loads(reply.stdout):
        if "UP" not in interface.get("flags", []):
            continue
        for item in interface.get("addr_info", []):
            if (
                item.get("local") == address
                and not item.get("tentative")
                and not item.get("dadfailed")
                and not {"tentative", "dadfailed"}.intersection(item.get("flags", []))
            ):
                candidates.append(
                    {"interface": interface["ifname"], "index": interface["ifindex"], "address": address}
                )
    if len(candidates) != 1:
        raise ValueError("The source address must belong to one active interface on this observation worker")
    worker = digest(
        {
            "host": socket.gethostname(),
            "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "network_namespace": os.stat("/proc/self/ns/net").st_ino,
        }
    )
    return {**candidates[0], "worker": worker}


def verify_probe(store, request_id):
    with store.tx() as tx:
        request = tx.get("probe_request", request_id)
        if request["status"] != "queued":
            return {"id": request_id, "status": request["status"]}
        source = tx.get("device", request["source_id"])
    binding = None
    try:
        if source.get("status") != "accepted" or request["source_address"] not in source.get("addresses", []):
            raise ValueError("Accept the measurement source and its address in inventory first")
        binding = local_binding(request["source_address"])
        status, detail = "verified", "Source address verified on the observation worker"
    except (ValueError, OSError, subprocess.SubprocessError):
        status, detail = (
            "unavailable",
            "The source must be accepted and assigned to an active worker interface",
        )
    with store.tx(write=True) as tx:
        current = tx.get("probe_request", request_id)
        if current["status"] != "queued":
            return {"id": request_id, "status": current["status"]}
        if identity(tx.get("device", source["id"])) != identity(source):
            status, detail = "unavailable", "The source identity changed during verification"
        # A newer request supersedes the earlier verification, even if it finishes first.
        latest = tx.get("probe", source["id"])
        if latest.get("request_id") != request_id:
            status, detail = "superseded", "A newer source verification was requested"
        elif status == "verified":
            tx.put(
                "probe",
                {
                    **latest,
                    "verified_local": True,
                    "source_address": request["source_address"],
                    "binding": binding,
                    "verified_at": now(),
                    "status": "verified",
                    "capabilities": ["H01", "H02", "H03", "H04"],
                },
                source["id"],
            )
        else:
            tx.put("probe", {**latest, "status": status, "verified_local": False}, source["id"])
        tx.put(
            "probe_request", {**current, "status": status, "detail": detail, "finished_at": now()}, request_id
        )
        tx.audit("observation", "probe.verified", source["id"], {"request_id": request_id, "status": status})
    return {"id": request_id, "status": status}
