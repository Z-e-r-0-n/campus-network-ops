import asyncio
import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.db import Conflict, now
from backend.diagnosis import service
from backend.diagnosis.gateway import GatewayFailure, request
from backend.diagnosis.models import RouteDraft
from backend.domain.models import Approval
from tests.test_check_workflow import post
from tests.test_security import configured


def route_body(connector_id="gateway", priority=10, model="example/model-a"):
    return RouteDraft(
        name="Reviewed free provider",
        connector_id=connector_id,
        model=model,
        response_model=model.split("/", 1)[1],
        provider="example",
        free_terms_url="https://example.org/free-terms",
        qualification_note="Isolated gateway with only this free connection and exact model; aliases and paid billing disabled.",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        free_only_gateway_verified=True,
        priority=priority,
    )


def setup_evidence(db, tmp_path, gateway_config=None):
    settings = Settings(state=tmp_path)
    (tmp_path / "secrets").mkdir(exist_ok=True)
    token = tmp_path / "secrets/test_gateway"
    token.write_text("test-token-not-real")
    token.chmod(0o600)
    with db.tx(write=True) as tx:
        tx.put(
            "connector",
            {
                "kind": "omniroute",
                "endpoint": "http://127.0.0.1:19999/v1",
                "pinned_address": "127.0.0.1",
                "secret_ref": "test_gateway",
                "status": "connected",
                "name": "Test gateway",
                **(gateway_config or {}),
            },
            "gateway",
        )
        for key in ["source", "target"]:
            tx.put(
                "device",
                {
                    "status": "accepted",
                    "label": key,
                    "addresses": ["192.0.2.1"],
                    "raw_config": "MUST NOT LEAVE",
                },
                key,
            )
        tx.put(
            "check_policy",
            {
                "check_id": "H03",
                "source_id": "source",
                "target_id": "target",
                "parameters": {"max_loss_percent": 2, "max_age_seconds": 900},
            },
            "policy",
        )
        tx.put(
            "check_result",
            {
                "policy_id": "policy",
                "source_id": "source",
                "target_id": "target",
                "status": "failed",
                "observed_at": now(),
                "metrics": {"loss_percent": 20, "password": "MUST NOT LEAVE", "raw_config": "MUST NOT LEAVE"},
                "evidence_ids": ["test-evidence"],
            },
            "measurement",
        )
        tx.put(
            "incident",
            {
                "status": "open",
                "source_id": "source",
                "target_id": "target",
                "policy_id": "policy",
                "summary": "MUST NOT LEAVE",
                "evidence_ids": ["test-evidence"],
            },
            "incident",
        )
        for priority, model in [(1, "example/model-a"), (2, "example/model-b")]:
            draft = service.route_draft(tx, route_body(priority=priority, model=model), "test")
            service.approve_route(
                tx, draft["id"], Approval(digest=draft["digest"], revision=draft["revision"]), "test"
            )
    return settings


def queued(db):
    with db.tx(write=True) as tx:
        item = service.preview(tx, {"kind": "incident", "id": "incident"}, "test")
        return service.start(
            tx, item["id"], Approval(digest=item["digest"], revision=item["revision"]), "test"
        )


def answer():
    return json.dumps(
        {
            "summary": "Loss was measured; its cause needs corroboration.",
            "hypotheses": [
                {
                    "explanation": "Congestion could explain the observed loss.",
                    "confidence": "low",
                    "citations": ["check_result:measurement"],
                }
            ],
            "alternatives": [],
            "missing_information": ["Interface error and discard counters"],
            "next_steps": [
                {
                    "kind": "physical_inspection",
                    "device_id": "target",
                    "description": "Inspect the affected link after checking counters.",
                    "citations": ["check_result:measurement"],
                    "runbook_id": None,
                }
            ],
        }
    )


def test_preview_requires_explicit_start_and_output_never_creates_plan(db, tmp_path, client):
    setup_evidence(db, tmp_path)
    configured(db, client)
    item = post(client, "/diagnosis-previews", {"kind": "incident", "id": "incident"}).json()
    assert item["status"] == "preview"
    assert "MUST NOT LEAVE" not in json.dumps(item["packet"])
    assert len(item["routes"]) == 2
    with db.tx() as tx:
        assert not tx.c.execute("SELECT 1 FROM outbox WHERE kind='diagnosis'").fetchone()
    started = post(
        client, f"/diagnoses/{item['id']}/start", {"digest": item["digest"], "revision": item["revision"]}
    )
    assert started.status_code == 200
    assert (
        post(
            client, f"/diagnoses/{item['id']}/start", {"digest": item["digest"], "revision": item["revision"]}
        ).status_code
        == 409
    )
    result = service.run(db, Settings(state=tmp_path), item["id"], lambda *args: answer())
    assert result["status"] == "complete"
    assert result["attempts"][0]["status"] == "received"
    assert (
        service.run(db, Settings(state=tmp_path), item["id"], lambda *args: pytest.fail("replayed"))["status"]
        == "complete"
    )
    with db.tx() as tx:
        assert not tx.list("plan") and not tx.list("execution")
    assert client.get("/api/v1/diagnosis-history/incident/incident").json()["items"][0]["current"]


def test_rollover_once_per_route_and_cooldown(db, tmp_path):
    settings = setup_evidence(db, tmp_path)
    item = queued(db)
    calls = []

    def transport(connector, token, route, packet):
        calls.append(route["model"])
        if len(calls) == 1:
            raise GatewayFailure("rate_limited", 3600)
        return answer()

    result = service.run(db, settings, item["id"], transport)
    assert result["status"] == "complete" and len(calls) == 2
    with db.tx() as tx:
        routes = service.available_routes(tx)
        assert len(routes) == 1 and routes[0]["content"]["model"] == "example/model-b"


@pytest.mark.parametrize("change", ["citation", "scope", "commands", "duplicate", "token", "truncated"])
def test_invalid_model_outputs_are_discarded(db, tmp_path, change):
    settings = setup_evidence(db, tmp_path)
    item = queued(db)
    raw = answer()
    if change == "citation":
        raw = raw.replace("check_result:measurement", "invented:evidence")
    elif change == "scope":
        raw = raw.replace('"target"', '"unrelated-device"')
    elif change == "commands":
        raw = raw[:-1] + ', "commands": ["configure terminal"]}'
    elif change == "duplicate":
        raw = raw[:-1] + ', "summary": "duplicate"}'
    elif change == "token":
        raw = raw.replace("Loss was measured", "sk-abcdefghijklmnopqrst")
    else:
        raw = raw[:40]
    result = service.run(db, settings, item["id"], lambda *args: raw)
    assert result["status"] == "unavailable"
    assert len(result["attempts"]) == 2
    assert "output" not in result
    assert all(a["reason"] == "invalid_diagnosis" for a in result["attempts"])


@pytest.mark.parametrize("change", ["cancel", "evidence", "route", "identity"])
def test_inflight_changes_cannot_publish_current_diagnosis(db, tmp_path, change):
    settings = setup_evidence(db, tmp_path)
    item = queued(db)

    def transport(*args):
        with db.tx(write=True) as tx:
            if change == "cancel":
                service.cancel(tx, item["id"], "test")
            elif change == "evidence":
                old = tx.get("check_result", "measurement")
                tx.put("check_result", {**old, "status": "passed"}, old["id"])
            elif change == "identity":
                old = tx.get("device", "target")
                tx.put("device", {**old, "status": "excluded"}, old["id"])
            else:
                old = tx.get("ai_route", item["routes"][0]["id"])
                tx.put("ai_route", {**old, "status": "paused"}, old["id"])
        return answer()

    result = service.run(db, settings, item["id"], transport)
    assert result["status"] == ("cancelled" if change == "cancel" else "stale")
    assert "output" not in result


def test_interruption_is_not_replayed_and_scheduler_expires_it(db, tmp_path):
    settings = setup_evidence(db, tmp_path)
    item = queued(db)
    with db.tx(write=True) as tx:
        tx.put("diagnosis", {**item, "status": "running", "owner": "lost-worker"}, item["id"])
    assert service.run(db, settings, item["id"], lambda *args: pytest.fail("replayed"))["status"] == "running"
    with db.tx(write=True) as tx:
        current = tx.get("diagnosis", item["id"])
        tx.put(
            "diagnosis",
            {**current, "deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            item["id"],
        )
        service.expire(tx)
        assert tx.get("diagnosis", item["id"])["status"] == "interrupted"


def test_no_eligible_route_and_stale_packet_do_not_send(db, tmp_path):
    setup_evidence(db, tmp_path)
    with db.tx(write=True) as tx:
        for route in tx.list("ai_route"):
            tx.put("ai_route", {**route, "status": "paused"}, route["id"])
        with pytest.raises(Conflict, match="No reviewed free route"):
            service.preview(tx, {"kind": "incident", "id": "incident"}, "test")
        result = tx.get("check_result", "measurement")
        tx.put("check_result", {**result, "observed_at": "2000-01-01T00:00:00+00:00"}, "measurement")
        with pytest.raises(Conflict, match="stale"):
            service.preview(tx, {"kind": "incident", "id": "incident"}, "test")


@pytest.mark.parametrize(
    "model", ["auto/free", "auto/general:free", "combo/preferred", "openrouter/auto", "example/*"]
)
def test_route_aliases_are_not_accepted(model):
    with pytest.raises(ValidationError):
        route_body(model=model)


@pytest.fixture
def gateway_server():
    class Handler(BaseHTTPRequestHandler):
        code = 200
        metadata = {
            "X-OmniRoute-Provider": "example",
            "X-OmniRoute-Model": "model-a",
            "X-OmniRoute-Response-Cost": "0.0000000000",
        }
        body = {"choices": [{"finish_reason": "stop", "message": {"content": answer()}}]}
        received = []
        delay = 0

        def do_POST(self):
            self.received.append(
                (
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["content-length"]))),
                    dict(self.headers),
                )
            )
            self.send_response(self.code)
            for key, value in self.metadata.items():
                self.send_header(key, value)
            self.end_headers()
            if self.delay:
                __import__("time").sleep(self.delay)
            try:
                self.wfile.write(json.dumps(self.body).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield (
        {"endpoint": f"http://gateway.invalid:{server.server_port}/v1", "pinned_address": "127.0.0.1"},
        Handler,
    )
    server.shutdown()
    server.server_close()
    thread.join()


def test_actual_http_pins_endpoint_and_sends_bounded_nonstreaming_packet(gateway_server):
    connector, handler = gateway_server
    raw = asyncio.run(request(connector, "test", route_body().model_dump(), {"observations": []}))
    assert raw == answer()
    path, body, headers = handler.received[0]
    assert path == "/v1/chat/completions" and body["model"] == "example/model-a"
    assert not body["stream"] and body["max_tokens"] == 3000
    assert headers["Host"].startswith("gateway.invalid:")
    assert headers["X-OmniRoute-No-Cache"] == "true"


@pytest.mark.parametrize(
    "header,value",
    [
        ("X-OmniRoute-Provider", "other"),
        ("X-OmniRoute-Model", "paid"),
        ("X-OmniRoute-Response-Cost", "0.01"),
        ("X-OmniRoute-Response-Cost", "NaN"),
        ("X-OmniRoute-Fallback-Attempts", "1"),
    ],
)
def test_gateway_metadata_violation_quarantines_route(gateway_server, header, value):
    connector, handler = gateway_server
    handler.metadata = {**handler.metadata, header: value}
    with pytest.raises(GatewayFailure) as exc:
        asyncio.run(request(connector, "test", route_body().model_dump(), {}))
    assert exc.value.quarantine


@pytest.mark.parametrize(
    "code,reason",
    [
        (429, "rate_limited"),
        (402, "free_quota_unavailable"),
        (503, "provider_unavailable"),
        (401, "gateway_access_rejected"),
        (302, "request_rejected"),
    ],
)
def test_http_failures_are_classified_without_saving_upstream_body(gateway_server, code, reason):
    connector, handler = gateway_server
    handler.code = code
    handler.body = {"error": "DO NOT STORE UPSTREAM CONTENT"}
    with pytest.raises(GatewayFailure, match=reason):
        asyncio.run(request(connector, "test", route_body().model_dump(), {}))


def test_route_review_api_and_pause(db, tmp_path, client):
    setup_evidence(db, tmp_path)
    configured(db, client)
    draft = post(client, "/ai-routes", route_body().model_dump(mode="json")).json()
    response = post(
        client,
        f"/ai-routes/{draft['id']}/approve",
        {"digest": draft["digest"], "revision": draft["revision"]},
    )
    assert response.status_code == 200
    approved = response.json()
    assert (
        post(client, f"/ai-routes/{draft['id']}/pause", revision=approved["revision"]).json()["status"]
        == "paused"
    )
    routes = client.get("/api/v1/ai-routes").json()["items"]
    assert not next(r for r in routes if r["id"] == draft["id"])["eligible"]


@pytest.mark.skipif(
    __import__("os").getenv("RUN_TEMPORAL_CONTRACT") != "1", reason="Opt-in Temporal contract"
)
async def test_temporal_diagnosis_with_real_gateway_http(db, tmp_path, gateway_server):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4

    from temporalio.client import Client
    from temporalio.worker import Worker

    from backend.workflows.runtime import Activities, ObservationWorkflow

    connector, handler = gateway_server
    settings = setup_evidence(db, tmp_path, connector)
    item = queued(db)
    client = await Client.connect(settings.temporal, namespace=settings.namespace)
    queue = "diagnosis-contract-" + uuid4().hex
    activities = Activities(db, settings)
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            client,
            task_queue=queue,
            workflows=[ObservationWorkflow],
            activities=[activities.observe],
            activity_executor=executor,
        ):
            result = await client.execute_workflow(
                ObservationWorkflow.run,
                {"kind": "diagnosis", "payload": {"diagnosis_id": item["id"]}},
                id=queue,
                task_queue=queue,
            )
    assert result["status"] == "complete"
    assert len(handler.received) == 1
    with db.tx() as tx:
        stored = tx.get("diagnosis", item["id"])
        assert stored["output"]["hypotheses"][0]["citations"] == ["check_result:measurement"]
        assert not tx.list("execution")


def test_total_gateway_deadline(gateway_server, monkeypatch):
    connector, handler = gateway_server
    handler.delay = 0.2
    monkeypatch.setattr("backend.diagnosis.gateway.REQUEST_SECONDS", 0.03)
    with pytest.raises(GatewayFailure, match="gateway_timeout_or_connection_failure"):
        asyncio.run(request(connector, "test", route_body().model_dump(), {}))


def test_gateway_oversized_body_is_rejected(gateway_server):
    connector, handler = gateway_server
    handler.body = {"untrusted": "x" * 70000}
    with pytest.raises(GatewayFailure, match="response_too_large"):
        asyncio.run(request(connector, "test", route_body().model_dump(), {}))


def test_expired_route_and_changed_connection_are_ineligible(db, tmp_path):
    setup_evidence(db, tmp_path)
    with db.tx(write=True) as tx:
        routes = service.available_routes(tx)
        first = routes[0]
        first["content"]["expires_at"] = "2000-01-01T00:00:00+00:00"
        first["digest"] = service.digest(first["content"])
        first = tx.put("ai_route", first, first["id"])
        assert not service.eligible(tx, first)
        connector = tx.get("connector", "gateway")
        tx.put("connector", {**connector, "pinned_address": "127.0.0.2"}, "gateway")
        assert not service.available_routes(tx)


def test_route_limit_and_packet_integrity(db, tmp_path):
    setup_evidence(db, tmp_path)
    with db.tx(write=True) as tx:
        item = service.preview(tx, {"kind": "incident", "id": "incident"}, "test")
        item["limits"]["maximum_calls"] = 99
        altered = tx.put("diagnosis", item, item["id"])
        with pytest.raises(Conflict):
            service.start(
                tx, altered["id"], Approval(digest=altered["digest"], revision=altered["revision"]), "test"
            )


def test_context_limits_topology_to_reviewed_in_scope_connections(db, tmp_path):
    setup_evidence(db, tmp_path)
    with db.tx(write=True) as tx:
        for key, status, target in [
            ("reviewed", "accepted", "target"),
            ("draft", "draft", "target"),
            ("outside", "accepted", "other"),
        ]:
            tx.put(
                "edge",
                {
                    "source_id": "source",
                    "target_id": target,
                    "layer": "advertised_neighbor",
                    "decision": "include",
                    "status": status,
                },
                key,
            )
        item = service.preview(tx, {"kind": "incident", "id": "incident"}, "test")
        assert [edge["id"] for edge in item["packet"]["connections"]] == ["reviewed"]
        assert item["packet"]["connections"][0]["layer"] == "advertised_neighbor"


def test_gateway_violation_stops_rollover_and_disables_other_routes(db, tmp_path):
    settings = setup_evidence(db, tmp_path)
    item = queued(db)
    calls = []

    def transport(*args):
        calls.append(True)
        raise GatewayFailure("unexpected_or_unknown_cost", quarantine=True)

    result = service.run(db, settings, item["id"], transport)
    assert result["status"] == "unavailable" and len(calls) == 1
    with db.tx() as tx:
        assert not service.available_routes(tx)
        assert tx.get("ai_gateway_health", "gateway")["quarantined"]
