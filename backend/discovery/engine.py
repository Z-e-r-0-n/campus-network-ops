"""Durable scoped frontier; observations never silently become accepted intent."""

from collections import deque
from ipaddress import ip_address

from psycopg.types.json import Jsonb

from backend.db import digest, now, uid
from backend.domain.models import Scope


class Discovery:
    def __init__(self, store):
        self.store = store

    def create(self, tx, request, actor):
        job = tx.put(
            "discovery",
            {
                **request,
                "status": "queued",
                "frontier": [[s, 0] for s in request["seeds"]],
                "visited": [],
                "gaps": [],
                "nodes": [],
                "exclusions": [],
                "started_at": None,
            },
        )
        tx.enqueue("discovery", {"run_id": job["id"]}, job["id"])
        tx.audit(
            actor, "discovery.requested", job["id"], {"seeds": request["seeds"], "scope": request["scope"]}
        )
        return job

    def run(self, run_id, reader):
        with self.store.tx(write=True) as tx:
            run = tx.get("discovery", run_id)
            if run["status"] in {"complete", "partial", "cancelled"}:
                return run
            run = tx.put(
                "discovery",
                {**run, "status": "running", "started_at": run["started_at"] or now(), "collector_id": uid()},
                run_id,
            )
        scope = Scope(**run["scope"])
        frontier, visited = deque(run["frontier"]), set(run["visited"])
        queued = {a for a, _ in frontier} | visited
        gaps, nodes = run["gaps"], run["nodes"]
        exclusions = run.get("exclusions", [])
        while frontier and len(visited) < scope.max_nodes:
            with self.store.tx() as tx:
                current = tx.get("discovery", run_id)
                if current["status"] == "cancelled" or current["collector_id"] != run["collector_id"]:
                    return current
            address, depth = frontier.popleft()
            if address in visited:
                continue
            if depth > scope.max_depth or not scope.permits(address):
                gaps.append({"address": address, "reason": "Depth or address scope boundary"})
                continue
            with self.store.tx() as tx:
                excluded = self.excluded(tx, address)
            if excluded:
                visited.add(address)
                exclusions.append(
                    {"address": address, "device_id": excluded["id"], "reason": "Excluded by operator"}
                )
                # Retain the skipped frontier position across a worker restart.
                with self.store.tx(write=True) as tx:
                    current = tx.get("discovery", run_id)
                    if current["status"] == "cancelled" or current["collector_id"] != run["collector_id"]:
                        return current
                    run = tx.put(
                        "discovery",
                        {
                            **run,
                            "frontier": list(frontier),
                            "visited": sorted(visited),
                            "exclusions": exclusions,
                        },
                        run_id,
                    )
                continue
            try:
                observation = reader(address)
            except Exception:
                # Transport errors can contain credentials; expose only a safe reason.
                observation = {
                    "addresses": [address],
                    "label": address,
                    "neighbors": [],
                    "gap": "Read failed; access or source needs review",
                    "source": "unavailable",
                }
            visited.add(address)
            with self.store.tx(write=True) as tx:
                # A read may finish after cancellation or after a replacement activity
                # took over. Neither may resurrect the old collector's checkpoint.
                current = tx.get("discovery", run_id)
                if current["status"] == "cancelled" or current["collector_id"] != run["collector_id"]:
                    return current
                evidence = tx.put(
                    "evidence",
                    {
                        "source": observation.get("source", "unknown"),
                        "observed_at": observation.get("observed_at"),
                        "collected_at": now(),
                        "observation": observation,
                        "run_id": run_id,
                    },
                )
                identity = self.identity(tx, address, observation, evidence["id"])
                if identity["status"] == "excluded":
                    # An exclusion may be added during the read, or recognized by
                    # a newly observed serial alias. Do not traverse its neighbors.
                    exclusions.append(
                        {
                            "address": address,
                            "device_id": identity["id"],
                            "reason": "Excluded identity observed",
                        }
                    )
                    run = tx.put(
                        "discovery",
                        {
                            **run,
                            "frontier": list(frontier),
                            "visited": sorted(visited),
                            "exclusions": exclusions,
                        },
                        run_id,
                    )
                    continue
                if identity["id"] not in nodes:
                    nodes.append(identity["id"])
                if observation.get("gap"):
                    gaps.append({"address": address, "reason": observation["gap"]})
                neighbors = observation.get("neighbors", [])
                if len(neighbors) > 256:
                    gaps.append(
                        {"address": address, "reason": "Neighbor response exceeded the per-device limit"}
                    )
                for neighbor in neighbors[:256]:
                    remote = neighbor.get("address")
                    try:
                        remote = str(ip_address(remote))
                    except (ValueError, TypeError):
                        gaps.append(
                            {"address": address, "reason": "Neighbor has no valid management IP address"}
                        )
                        continue
                    edge = {
                        "source_id": identity["id"],
                        "remote_address": remote,
                        "local_port": neighbor.get("local_port", ""),
                        "remote_port": neighbor.get("remote_port", ""),
                        "layer": "advertised_neighbor",
                        "protocol": neighbor.get("protocol", "unknown"),
                        "evidence_ids": [evidence["id"]],
                        "observed_at": neighbor.get("observed_at", observation.get("observed_at")),
                        "source": neighbor.get("source", observation.get("source")),
                        "status": "draft",
                    }
                    edge_key = digest(
                        {
                            k: edge[k]
                            for k in (
                                "source_id",
                                "remote_address",
                                "local_port",
                                "layer",
                                "protocol",
                                "source",
                            )
                        }
                    )
                    previous = tx.get("edge", edge_key, False)
                    if previous and previous.get("reviewed_by"):
                        # A new advertisement is evidence, not permission to replace
                        # an operator's physical/logical classification or rejection.
                        edge = {
                            **previous,
                            "latest_observation": edge,
                            "last_seen_at": observation.get("observed_at"),
                            "evidence_ids": list(
                                dict.fromkeys(previous.get("evidence_ids", []) + [evidence["id"]])
                            )[-20:],
                        }
                    tx.put("edge", edge, edge_key)
                    if remote not in queued:
                        queued.add(remote)
                        if depth + 1 > scope.max_depth or not scope.permits(remote):
                            gaps.append({"address": remote, "reason": "Depth or address scope boundary"})
                        elif len(frontier) >= scope.max_nodes:
                            gaps.append(
                                {
                                    "address": remote,
                                    "reason": "Frontier limit reached; narrow discovery scope",
                                }
                            )
                        else:
                            frontier.append((remote, depth + 1))
                gaps = gaps[:1000]
                run = tx.put(
                    "discovery",
                    {
                        **run,
                        "frontier": list(frontier),
                        "visited": sorted(visited),
                        "gaps": gaps,
                        "nodes": nodes,
                    },
                    run_id,
                )
        if frontier:
            gaps.append({"reason": "Node budget reached; remaining frontier retained"})
        with self.store.tx(write=True) as tx:
            current = tx.get("discovery", run_id)
            if current["status"] == "cancelled" or current["collector_id"] != run["collector_id"]:
                return current
            run = tx.put(
                "discovery",
                {
                    **run,
                    "status": "partial" if gaps else "complete",
                    "frontier": list(frontier),
                    "gaps": gaps,
                    "finished_at": now(),
                },
                run_id,
            )
            tx.audit(
                "discovery", "discovery.finished", run_id, {"status": run["status"], "nodes": len(nodes)}
            )
        return run

    @staticmethod
    def excluded(tx, address, observation=None):
        observation = observation or {}
        serial = observation.get("serial")
        if not serial or serial.lower() in {"unknown", "none", "n/a"}:
            serial = None
        row = tx.c.execute(
            "SELECT * FROM records WHERE kind='device' AND data->>'status'='excluded' "
            "AND (data->'addresses' @> %s OR (data->>'serial'=%s AND data->>'platform'=%s)) "
            "ORDER BY id LIMIT 1",
            (Jsonb([address]), serial, observation.get("platform")),
        ).fetchone()
        return tx.unpack(row) if row else None

    @staticmethod
    def identity(tx, address, observation, evidence_id):
        excluded = Discovery.excluded(tx, address, observation)
        if excluded:
            return tx.put(
                "device",
                {
                    **excluded,
                    "addresses": sorted(set(excluded.get("addresses", []) + [address])),
                    "last_excluded_evidence_id": evidence_id,
                },
                excluded["id"],
            )
        # Search indexed JSON containment at the DB; do not truncate identity resolution at a UI page size.
        rows = tx.c.execute(
            "SELECT * FROM records WHERE kind='device' AND data->>'status' NOT IN ('merged','excluded') AND data->'addresses' @> %s",
            (Jsonb([address]),),
        ).fetchall()
        existing = [tx.unpack(row) for row in rows]
        old = existing[0] if len(existing) == 1 else None
        serial = observation.get("serial")
        serial = serial if serial and serial.lower() not in {"unknown", "none", "n/a"} else None
        conflict = bool(len(rows) > 1 or (old and old.get("serial") and serial and old["serial"] != serial))
        if conflict:
            for affected in existing:
                tx.put("device", {**affected, "status": "conflict"}, affected["id"])
            matches = [
                item
                for item in existing
                if item.get("serial") == serial
                and serial
                and item.get("platform") == observation.get("platform")
            ]
            old = matches[0] if len(matches) == 1 else None
        if not old and serial and not conflict:
            candidates = tx.c.execute(
                "SELECT * FROM records WHERE kind='device' AND data->>'status' NOT IN ('merged','excluded') AND data->>'serial'=%s AND data->>'platform'=%s",
                (serial, observation.get("platform", "unknown")),
            ).fetchall()
            if len(candidates) == 1:
                old = tx.unpack(candidates[0])
        device_id = old["id"] if old else uid()
        identity = {k: (old or {}).get(k) for k in ("platform", "model", "firmware", "serial")}
        provenance = dict((old or {}).get("identity_sources", {}))
        for key, value in {**observation, "serial": serial}.items():
            if key not in identity or value in (None, "", "unknown"):
                continue
            previous = provenance.get(key, {})
            if previous.get("source") == "ssh" and observation.get("source") != "ssh":
                continue
            identity[key] = value
            provenance[key] = {
                "source": observation.get("source", "unknown"),
                "observed_at": observation.get("observed_at"),
                "evidence_id": evidence_id,
            }
        data = {
            **(old or {}),
            "label": (old or {}).get("label") or observation.get("label", address),
            "addresses": sorted(set((old or {}).get("addresses", []) + [address])),
            "role": (old or {}).get("role", observation.get("role", "unknown")),
            **identity,
            "identity_sources": provenance,
            "status": "conflict" if conflict else (old or {}).get("status", "draft"),
            "last_evidence_id": evidence_id,
            "observed_at": observation.get("observed_at"),
            "access": (old or {}).get("access", "not_configured"),
        }
        return tx.put("device", data, device_id)
