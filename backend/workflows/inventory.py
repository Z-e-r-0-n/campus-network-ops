"""Separate intended-inventory queue. Device observation never publishes intent."""

from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from backend.connectors.netbox import NetBox, publish_baseline
    from backend.db import Conflict, now


@workflow.defn
class InventoryWorkflow:
    @workflow.run
    async def run(self, job: dict):
        return await workflow.execute_activity(
            "publish_inventory",
            job,
            start_to_close_timeout=timedelta(minutes=15),
            schedule_to_close_timeout=timedelta(hours=1),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30)),
        )


class InventoryActivities:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    @activity.defn(name="publish_inventory")
    def publish(self, job: dict):
        if job["kind"] == "inventory_publication":
            result = publish_baseline(self.db, self.settings, job["payload"]["baseline_id"])
            return {"id": result["id"], "status": result["status"]}
        if job["kind"] not in {"inventory_options", "inventory_object"}:
            raise ValueError("Unsupported inventory work")
        with self.db.tx() as tx:
            request = tx.get("inventory_request", job["payload"]["request_id"])
            connector = tx.get("connector", request["connector_id"])
        if request["status"] == "complete":
            return {"id": request["id"], "status": "complete"}
        try:
            if connector["kind"] != "netbox" or connector["revision"] != request["connector_revision"]:
                raise Conflict("Intended inventory connection changed")
            client = NetBox(connector, self.settings.secret(connector["secret_ref"]))
            result = client.create_object(request) if job["kind"] == "inventory_object" else None
            options = client.options()
            with self.db.tx(write=True) as tx:
                tx.put(
                    "inventory_options",
                    {"connector_id": connector["id"], "options": options, "collected_at": now()},
                    connector["id"],
                )
                tx.put(
                    "inventory_request",
                    {**request, "status": "complete", "result": result, "finished_at": now()},
                    request["id"],
                )
                tx.audit("inventory", "inventory_request.completed", request["id"])
            return {"id": request["id"], "status": "complete"}
        except Exception:
            with self.db.tx(write=True) as tx:
                tx.put(
                    "inventory_request",
                    {
                        **request,
                        "status": "failed",
                        "finished_at": now(),
                        "detail": "Intended inventory could not complete this request. Check connection access and conflicting records.",
                    },
                    request["id"],
                )
            return {"id": request["id"], "status": "failed"}
