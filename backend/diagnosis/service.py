"""Reviewed routes and durable diagnosis attempts, separate from maintenance execution."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from backend.db import Conflict, Missing, digest, now, uid
from backend.diagnosis.context import packet, still_current
from backend.diagnosis.gateway import GatewayFailure, request
from backend.diagnosis.models import Diagnosis
from backend.security.redaction import redact

TERMINAL = {"complete", "stale", "cancelled", "unavailable", "interrupted"}


def later(seconds):
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def connector_identity(connector):
    return {key: connector.get(key) for key in ("id", "kind", "endpoint", "pinned_address", "secret_ref")}


def route_draft(tx, body, actor):
    count = tx.c.execute("SELECT count(*) AS n FROM records WHERE kind='ai_route'").fetchone()["n"]
    if count >= 100:
        raise Conflict("This installation supports up to 100 reviewed route records")
    connector = tx.get("connector", body.connector_id)
    if connector["kind"] != "omniroute":
        raise Conflict("Choose an OmniRoute connection")
    content = {**body.model_dump(mode="json"), "connector": connector_identity(connector)}
    if redact(body.model_dump(mode="json")) != body.model_dump(mode="json"):
        raise ValueError("Route notes must not contain credentials")
    item = tx.put(
        "ai_route", {"content": content, "digest": digest(content), "status": "draft", "created_by": actor}
    )
    tx.audit(actor, "ai.route_prepared", item["id"], {"digest": item["digest"]})
    return item


def approve_route(tx, key, approval, actor):
    route = tx.get("ai_route", key)
    if (
        digest(route["content"]) != route["digest"]
        or route["status"] != "draft"
        or route["digest"] != approval.digest
        or route["revision"] != approval.revision
    ):
        raise Conflict("Review the current route before approving it")
    if datetime.fromisoformat(route["content"]["expires_at"]) <= datetime.now(UTC):
        raise Conflict("Route eligibility has expired")
    connector = tx.get("connector", route["content"]["connector_id"])
    if connector_identity(connector) != route["content"]["connector"]:
        raise Conflict("The gateway connection changed")
    result = tx.put(
        "ai_route", {**route, "status": "approved", "approved_by": actor, "approved_at": now()}, key
    )
    tx.audit(actor, "ai.route_approved", key, {"digest": route["digest"]})
    return result


def eligible(tx, route, revision=None):
    if not route or route.get("status") != "approved" or (revision and route["revision"] != revision):
        return False
    if digest(route["content"]) != route["digest"]:
        return False
    try:
        if datetime.fromisoformat(route["content"]["expires_at"]) <= datetime.now(UTC):
            return False
        connector = tx.get("connector", route["content"]["connector_id"])
        if connector_identity(connector) != route["content"]["connector"]:
            return False
        gateway_health = tx.get("ai_gateway_health", connector["id"], False) or {}
        if gateway_health.get("quarantined") and gateway_health.get("identity") == connector_identity(
            connector
        ):
            return False
        health = tx.get("ai_route_health", route["id"], False) or {}
        return not health.get("quarantined") and (
            not health.get("retry_at") or datetime.fromisoformat(health["retry_at"]) <= datetime.now(UTC)
        )
    except (Missing, ValueError, TypeError):
        return False


def available_routes(tx):
    rows = tx.c.execute(
        "SELECT * FROM records WHERE kind='ai_route' AND data->>'status'='approved' ORDER BY (data->'content'->>'priority')::integer, id"
    ).fetchall()
    return [r for row in rows if eligible(tx, r := tx.unpack(row))][:3]


def preview(tx, subject, actor):
    evidence = packet(tx, subject)
    routes = available_routes(tx)
    if not routes:
        raise Conflict("No reviewed free route is currently eligible; configure routes in Settings")
    content = {
        "subject": subject,
        "packet": evidence,
        "routes": [
            {
                "id": r["id"],
                "revision": r["revision"],
                "name": r["content"]["name"],
                "model": r["content"]["model"],
                "provider": r["content"]["provider"],
            }
            for r in routes
        ],
        "limits": {"maximum_calls": len(routes), "seconds_per_call": 60, "output_tokens_per_call": 3000},
    }
    item = tx.put(
        "diagnosis",
        {
            **content,
            "digest": digest(content),
            "status": "preview",
            "expires_at": later(600),
            "attempts": [],
            "created_by": actor,
        },
    )
    tx.audit(actor, "diagnosis.prepared", item["id"], {"digest": item["digest"]})
    return item


def start(tx, key, approval, actor):
    item = tx.get("diagnosis", key)
    if (
        digest({k: item[k] for k in ("subject", "packet", "routes", "limits")}) != item["digest"]
        or item["status"] != "preview"
        or item["revision"] != approval.revision
        or item["digest"] != approval.digest
    ):
        raise Conflict("Review the current evidence packet before sending it")
    if datetime.fromisoformat(item["expires_at"]) <= datetime.now(UTC) or not still_current(tx, item):
        raise Conflict("The evidence changed or the preview expired; prepare a new diagnosis")
    if not all(eligible(tx, tx.get("ai_route", r["id"]), r["revision"]) for r in item["routes"]):
        raise Conflict("Route eligibility changed; prepare a new diagnosis")
    active = tx.c.execute(
        "SELECT count(*) AS n FROM records WHERE kind='diagnosis' AND data->>'status' IN ('queued','running') AND (data->>'deadline')::timestamptz>now()"
    ).fetchone()["n"]
    if active >= 4:
        raise Conflict("Four diagnoses are already pending; wait for a result")
    recent = tx.c.execute(
        "SELECT 1 FROM records WHERE kind='diagnosis' AND data->'subject'->>'id'=%s AND (data->>'started_at')::timestamptz>now()-interval '60 seconds' LIMIT 1",
        (item["subject"]["id"],),
    ).fetchone()
    if recent:
        raise Conflict("Wait one minute between diagnoses of the same finding")
    result = tx.put(
        "diagnosis",
        {**item, "status": "queued", "started_at": now(), "deadline": later(300), "requested_by": actor},
        key,
    )
    tx.enqueue("diagnosis", {"diagnosis_id": key}, key)
    tx.audit(actor, "diagnosis.requested", key, {"digest": item["digest"]})
    return result


def cancel(tx, key, actor):
    item = tx.get("diagnosis", key)
    if item["status"] in TERMINAL:
        return item
    result = tx.put("diagnosis", {**item, "status": "cancelled", "finished_at": now()}, key)
    tx.audit(actor, "diagnosis.cancelled", key)
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def validate_output(raw, evidence):
    if not isinstance(raw, str) or len(raw.encode()) > 32000:
        raise GatewayFailure("invalid_diagnosis")
    try:
        parsed = json.loads(raw, object_pairs_hook=unique_object)
        output = Diagnosis.model_validate(parsed).model_dump()
        if redact(output) != output:
            raise ValueError("Sensitive output")
        citations = {o["citation"]: o for o in evidence["observations"]}
        devices = {d["id"] for d in evidence["devices"]}
        for claim in [*output["hypotheses"], *output["alternatives"], *output["next_steps"]]:
            if any(c not in citations for c in claim["citations"]):
                raise ValueError("Unknown citation")
            if not any(citations[c]["fresh"] for c in claim["citations"]):
                raise ValueError("Only stale support")
            if "device_id" in claim and claim["device_id"] not in devices:
                raise ValueError("Out-of-scope device")
        return output
    except (ValueError, TypeError, ValidationError):
        raise GatewayFailure("invalid_diagnosis") from None


def finish(tx, item, status, **extra):
    result = tx.put("diagnosis", {**item, **extra, "status": status, "finished_at": now()}, item["id"])
    tx.audit("diagnosis", "diagnosis.finished", item["id"], {"status": status})
    return result


def run(store, settings, key, transport=None):
    transport = transport or (
        lambda connector, token, route, evidence: asyncio.run(request(connector, token, route, evidence))
    )
    with store.tx(write=True) as tx:
        item = tx.get("diagnosis", key)
        if item["status"] in TERMINAL or item["status"] == "preview":
            return item
        if item["status"] == "running":
            # An interrupted call may already have consumed quota. Never replay it.
            if datetime.fromisoformat(item["deadline"]) <= datetime.now(UTC):
                return finish(
                    tx,
                    item,
                    "interrupted",
                    reason="Previous request outcome is unknown; request a new diagnosis",
                )
            return item
        if datetime.fromisoformat(item["deadline"]) <= datetime.now(UTC) or not still_current(tx, item):
            return finish(tx, item, "stale")
        item = tx.put("diagnosis", {**item, "status": "running", "owner": uid()}, key)
    owner = item["owner"]
    for selected in item["routes"]:
        with store.tx(write=True) as tx:
            item = tx.get("diagnosis", key)
            if item["status"] != "running" or item.get("owner") != owner:
                return item
            if datetime.fromisoformat(item["deadline"]) <= datetime.now(UTC) or not still_current(tx, item):
                return finish(tx, item, "stale")
            route = tx.get("ai_route", selected["id"])
            if not eligible(tx, route, selected["revision"]):
                continue
            connector = tx.get("connector", route["content"]["connector_id"])
            attempt = {"id": uid(), "route_id": route["id"], "started_at": now(), "status": "calling"}
            item = tx.put("diagnosis", {**item, "attempts": [*item["attempts"], attempt]}, key)
        output, failure = None, None
        try:
            try:
                token = settings.secret(connector["secret_ref"])
            except (OSError, ValueError):
                raise GatewayFailure("gateway_credential_unavailable", quarantine=True) from None
            raw = transport(connector, token, route["content"], item["packet"])
            output = validate_output(raw, item["packet"])
        except GatewayFailure as exc:
            failure = exc
        except Exception:
            # Never persist transport exceptions or model text that failed validation.
            failure = GatewayFailure("diagnosis_processing_failed")
        with store.tx(write=True) as tx:
            current = tx.get("diagnosis", key)
            if current.get("owner") != owner:
                return current
            attempts = current["attempts"]
            attempts[-1] = {
                **attempts[-1],
                "finished_at": now(),
                "status": "rejected" if failure else "received",
                "reason": failure.reason if failure else None,
            }
            current = tx.put("diagnosis", {**current, "attempts": attempts}, key)
            if failure:
                tx.put(
                    "ai_route_health",
                    {
                        "reason": failure.reason,
                        "retry_at": later(failure.cooldown),
                        "quarantined": failure.quarantine,
                        "at": now(),
                    },
                    route["id"],
                )
            if failure and failure.quarantine:
                tx.put(
                    "ai_gateway_health",
                    {
                        "quarantined": True,
                        "reason": failure.reason,
                        "identity": connector_identity(connector),
                        "at": now(),
                    },
                    connector["id"],
                )
            if current["status"] != "running":
                return current
            if failure and failure.quarantine:
                return finish(
                    tx,
                    current,
                    "unavailable",
                    reason="Gateway access or routing qualification needs review; rollover stopped",
                )
            if datetime.fromisoformat(current["deadline"]) <= datetime.now(UTC) or not still_current(
                tx, current
            ):
                return finish(tx, current, "stale")
            latest_route = tx.get("ai_route", route["id"])
            if not failure and not eligible(tx, latest_route, selected["revision"]):
                return finish(tx, current, "stale", reason="The approved route changed during the request")
            if output:
                tx.put("ai_route_health", {"at": now(), "quarantined": False}, route["id"])
                return finish(tx, current, "complete", output=output, route_id=route["id"])
    with store.tx(write=True) as tx:
        current = tx.get("diagnosis", key)
        return (
            current
            if current["status"] != "running"
            else finish(tx, current, "unavailable", reason="No reviewed route returned a usable diagnosis")
        )


def expire(tx):
    rows = tx.c.execute(
        "SELECT * FROM records WHERE kind='diagnosis' AND data->>'status' IN ('queued','running') AND (data->>'deadline')::timestamptz<=now() LIMIT 100"
    ).fetchall()
    for row in rows:
        finish(tx, tx.unpack(row), "interrupted", reason="Request deadline elapsed; no automatic replay")
