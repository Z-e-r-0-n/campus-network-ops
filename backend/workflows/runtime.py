"""Temporal receives record IDs only. Credentials never enter workflow histories."""

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from backend.checks.collect import run_check
    from backend.checks.policies import enqueue as enqueue_check
    from backend.checks.probes import verify_probe
    from backend.config import Settings
    from backend.connectors.http import PinnedHTTP
    from backend.connectors.librenms import LibreNMS, SnapshotReader
    from backend.connectors.netbox import authorization
    from backend.db import Conflict, Store, now
    from backend.diagnosis.service import expire as expire_diagnoses
    from backend.diagnosis.service import run as run_diagnosis
    from backend.discovery.access import DirectReader, scan_host
    from backend.discovery.engine import Discovery
    from backend.discovery.keys import prepare_profile_key
    from backend.discovery.sources import CombinedReader
    from backend.reviews.rules import run_review
    from backend.workflows.inventory import InventoryActivities, InventoryWorkflow

OBSERVATION_KINDS = [
    "diagnosis",
    "discovery",
    "connector_check",
    "configuration_review",
    "check",
    "access_key",
    "host_scan",
    "probe_verification",
]
INVENTORY_KINDS = ["inventory_publication", "inventory_options", "inventory_object"]


@workflow.defn
class ObservationWorkflow:
    @workflow.run
    async def run(self, job: dict):
        return await workflow.execute_activity(
            "observe",
            job,
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30)),
            schedule_to_close_timeout=timedelta(hours=1),
        )


class Activities:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    @activity.defn(name="observe")
    def observe(self, job: dict):
        kind, payload = job["kind"], job["payload"]
        if kind == "diagnosis":
            result = run_diagnosis(self.db, self.settings, payload["diagnosis_id"])
            return {"id": result["id"], "status": result["status"]}
        if kind == "probe_verification":
            return verify_probe(self.db, payload["request_id"])
        if kind == "access_key":
            return prepare_profile_key(self.db, self.settings, payload["profile_id"])
        if kind == "host_scan":
            return scan_host(self.db, payload["trust_id"])
        if kind == "discovery":
            with self.db.tx() as tx:
                run = tx.get("discovery", payload["run_id"])
                connector = tx.get("connector", run["connector_id"]) if run.get("connector_id") else None
            monitoring = None
            source_gap = None
            if connector:
                try:
                    if connector["kind"] != "librenms":
                        raise ValueError("Unsupported discovery source")
                    snapshot = LibreNMS(connector, self.settings.secret(connector["secret_ref"])).snapshot()
                    zone = connector["source_timezone"] if connector.get("source_timezone_verified") else None
                    monitoring = SnapshotReader(snapshot, zone)
                except Exception:
                    source_gap = (
                        "Monitoring source unavailable; discovery retained its direct-access coverage"
                    )
            direct = DirectReader(self.db, self.settings, run.get("access_profile_ids", []))
            completed = Discovery(self.db).run(run["id"], CombinedReader(direct, monitoring, source_gap))
            return {"id": completed["id"], "status": completed["status"]}
        if kind == "connector_check":
            with self.db.tx() as tx:
                run = tx.get("connector_check", payload["run_id"])
                connector = tx.get("connector", run["connector_id"])
            try:
                token = self.settings.secret(connector["secret_ref"])
                if connector["kind"] == "librenms":
                    LibreNMS(connector, token).snapshot()
                else:
                    route = {"netbox": "/status/", "omniroute": "/models", "oxidized": "/nodes.json"}[
                        connector["kind"]
                    ]
                    header = (
                        authorization(token)
                        if connector["kind"] == "netbox"
                        else {"Authorization": "Bearer " + token}
                    )
                    PinnedHTTP(connector, header).request("GET", route)
                status, detail = "connected", "Source returned a valid response"
            except Exception:
                # Do not put upstream exceptions/headers in Temporal history or application records.
                status, detail = "unavailable", "Connection, access or source format needs review"
            with self.db.tx(write=True) as tx:
                current = tx.get("connector", connector["id"])
                tx.put(
                    "connector",
                    {**current, "status": status, "last_checked_at": now(), "detail": detail},
                    connector["id"],
                )
                tx.put("connector_check", {**run, "status": status, "finished_at": now()}, run["id"])
                tx.audit("connector", "connector.checked", connector["id"], {"status": status})
            return {"status": status}
        if kind == "configuration_review":
            review = run_review(self.db)
            with self.db.tx(write=True) as tx:
                tx.put(
                    "review_request", {"status": "complete", "review_id": review["id"]}, payload["request_id"]
                )
            return {"id": review["id"], "status": review["status"]}
        if kind == "check":
            # Jobs created before reviewed scheduling are not authorization to probe.
            if not payload.get("run_id"):
                return {"status": "expired"}
            check = run_check(self.db, payload["policy_id"], payload["run_id"])
            return {"id": check["id"], "status": check["status"]}
        raise ValueError("Unknown observation job")


def schedule(db):
    instant = datetime.now(timezone.utc)
    with db.tx(write=True) as tx:
        expire_diagnoses(tx)
        rows = tx.c.execute(
            "SELECT * FROM records WHERE kind='check_policy' AND data->>'enabled'='true' "
            "AND (data->>'next_due')::timestamptz<=%s ORDER BY data->>'next_due',id LIMIT 100",
            (instant,),
        ).fetchall()
        for row in rows:
            policy = tx.unpack(row)
            try:
                enqueue_check(tx, policy, instant=instant)
            except Conflict as exc:
                tx.put("check_policy", {**policy, "enabled": False, "hold_reason": str(exc)}, policy["id"])
                tx.audit("scheduler", "check.review_required", policy["id"], {"reason": str(exc)})
                continue
            current = tx.get("check_policy", policy["id"])
            tx.put(
                "check_policy",
                {
                    **current,
                    "next_due": (instant + timedelta(seconds=policy["interval_seconds"])).isoformat(),
                },
                policy["id"],
            )


async def serve(inventory=False):
    settings = Settings()
    db = Store(settings.dsn)
    db.migrate()
    role = "inventory" if inventory else "observation"
    queue = "campus-next-inventory" if inventory else "campus-next-observe"
    workflow_type = InventoryWorkflow if inventory else ObservationWorkflow
    kinds = INVENTORY_KINDS if inventory else OBSERVATION_KINDS
    while True:
        try:
            client = await Client.connect(settings.temporal, namespace=settings.namespace)
            activities = InventoryActivities(db, settings) if inventory else Activities(db, settings)
            with ThreadPoolExecutor(max_workers=4) as executor:
                async with Worker(
                    client,
                    task_queue=queue,
                    workflows=[workflow_type],
                    activities=[activities.publish if inventory else activities.observe],
                    activity_executor=executor,
                    max_concurrent_activities=4,
                ):
                    while True:
                        if not inventory:
                            await asyncio.to_thread(schedule, db)
                        with db.tx() as tx:
                            jobs = tx.c.execute(
                                "SELECT id,kind,payload FROM outbox WHERE dispatched IS NULL AND kind=ANY(%s) ORDER BY created LIMIT 50",
                                (kinds,),
                            ).fetchall()
                        for job in jobs:
                            try:
                                await client.start_workflow(
                                    workflow_type.run,
                                    job,
                                    id=("inventory" if inventory else "observe") + "-" + job["id"],
                                    task_queue=queue,
                                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                                    execution_timeout=timedelta(hours=2),
                                )
                            except WorkflowAlreadyStartedError:
                                pass
                            with db.tx(write=True) as tx:
                                tx.c.execute("UPDATE outbox SET dispatched=now() WHERE id=%s", (job["id"],))
                        with db.tx(write=True) as tx:
                            tx.put(
                                "system",
                                {
                                    "status": "connected",
                                    "at": now(),
                                    "engine": "Temporal",
                                    "queue": queue,
                                },
                                role + "_worker",
                            )
                        await asyncio.sleep(2)
        except (OSError, RuntimeError):
            with db.tx(write=True) as tx:
                tx.put("system", {"status": "unavailable", "at": now()}, role + "_worker")
            await asyncio.sleep(10)


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(serve(inventory="inventory" in sys.argv[1:]))
