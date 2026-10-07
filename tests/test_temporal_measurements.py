"""Opt-in contract: real Temporal activity, PostgreSQL and loopback ICMP."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from backend.checks.policies import approve, enqueue, prepare
from backend.config import Settings
from backend.db import now
from backend.domain.models import Approval
from backend.workflows.runtime import Activities, ObservationWorkflow


@pytest.mark.skipif(os.getenv("RUN_TEMPORAL_CONTRACT") != "1", reason="Opt-in Temporal contract")
async def test_real_worker_verifies_source_then_collects_approved_loopback(db, tmp_path):
    settings = Settings(state=tmp_path)
    client = await Client.connect(settings.temporal, namespace=settings.namespace)
    queue = "measurement-contract-" + uuid4().hex
    with db.tx(write=True) as tx:
        for name in ("source", "target"):
            tx.put(
                "device",
                {"label": name, "role": "probe", "addresses": ["127.0.0.1"], "status": "accepted"},
                name,
            )
        request = tx.put(
            "probe_request", {"source_id": "source", "source_address": "127.0.0.1", "status": "queued"}
        )
        tx.put("probe", {"request_id": request["id"], "verified_local": False}, "source")
    activities = Activities(db, settings)
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            client,
            task_queue=queue,
            workflows=[ObservationWorkflow],
            activities=[activities.observe],
            activity_executor=executor,
        ):
            verified = await client.execute_workflow(
                ObservationWorkflow.run,
                {"kind": "probe_verification", "payload": {"request_id": request["id"]}},
                id=queue + "-source",
                task_queue=queue,
            )
            assert verified["status"] == "verified"
            with db.tx(write=True) as tx:
                policy = tx.put(
                    "check_policy",
                    {
                        "source_id": "source",
                        "target_id": "target",
                        "target_address": "127.0.0.1",
                        "check_id": "H01",
                        "parameters": {"samples": 2, "max_loss_percent": 0},
                        "interval_seconds": 60,
                        "enabled": False,
                        "next_due": now(),
                    },
                )
                review = prepare(tx, policy["id"], "contract")
                enabled = approve(
                    tx,
                    review["id"],
                    Approval(digest=review["digest"], revision=review["revision"]),
                    "contract",
                )
                run = enqueue(tx, enabled, "contract")
                job = tx.c.execute("SELECT kind,payload FROM outbox WHERE id=%s", (run["id"],)).fetchone()
            result = await client.execute_workflow(
                ObservationWorkflow.run, job, id=queue + "-check", task_queue=queue
            )
            assert result["status"] == "passed"
            with db.tx() as tx:
                observed = tx.get("check_result", result["id"])
                assert observed["metrics"]["received"] == 2
                assert tx.get("check_run", run["id"])["status"] == "complete"
                evidence = tx.get("evidence", observed["evidence_ids"][0])
                assert evidence["measurement"]["source"] == "127.0.0.1"
                assert evidence["measurement"]["scope"] == "controller-origin ICMP"
