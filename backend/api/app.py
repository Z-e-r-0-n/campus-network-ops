import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.checks import policies as measurement_policies
from backend.config import Settings
from backend.db import Conflict, Missing, Store, canonical, digest, now, uid
from backend.diagnosis import service as diagnosis_service
from backend.diagnosis.models import RouteDraft, Subject
from backend.discovery.access import eligible
from backend.discovery.engine import Discovery
from backend.discovery.review import (
    BaselineDraft,
    EdgeEdit,
    IdentityDisposition,
    InventoryObject,
    MergeDevices,
    SplitDevice,
    approve_baseline,
    merge_devices,
    prepare_baseline,
    save_edge,
    split_device,
)
from backend.domain.models import (
    AccessProfile,
    AccessRetry,
    Approval,
    CheckPolicy,
    ConnectorRequest,
    DeviceEdit,
    DiscoveryRequest,
    Disposition,
    Login,
    PasswordConfirmation,
    ProbeRequest,
    Setup,
    TrustApproval,
    TrustRequest,
)
from backend.execution.plans import RepairRequest, approve, compile_plan
from backend.security.auth import Auth, Denied

PUBLIC_LISTS = {
    "diagnoses": "diagnosis",
    "devices": "device",
    "discovery-runs": "discovery",
    "connectors": "connector",
    "check-policies": "check_policy",
    "check-results": "check_result",
    "check-reviews": "check_review",
    "check-runs": "check_run",
    "probes": "probe",
    "probe-requests": "probe_request",
    "incidents": "incident",
    "recommendations": "recommendation",
    "repair-plans": "plan",
    "executions": "execution",
    "configuration-reviews": "review",
    "evidence": "evidence",
    "baseline-drafts": "baseline",
    "notifications": "notification",
    "services": "service",
    "access-profiles": "access_profile",
    "host-identities": "host_trust",
    "access-holds": "access_hold",
    "connections": "edge",
    "inventory-options": "inventory_options",
    "inventory-requests": "inventory_request",
}
ROOT = Path(__file__).resolve().parents[2]


def create_app(settings=None, store=None):
    settings = settings or Settings()
    db = store or Store(settings.dsn)
    auth = Auth(db)

    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        yield

    app = FastAPI(
        title="Campus network operations",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.db, app.state.settings = db, settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.origin).hostname])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "code": "invalid_request",
                "message": "Check the highlighted fields.",
                "fields": [
                    {"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()
                ],
            },
            status_code=422,
        )

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"code": "invalid_request", "message": str(exc)}, status_code=422)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"code": "conflict", "message": str(exc)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse({"code": "not_found", "message": str(exc)}, status_code=404)

    @app.exception_handler(Denied)
    async def denied(request, exc):
        return JSONResponse({"code": "access_denied", "message": str(exc)}, status_code=exc.status)

    @app.middleware("http")
    async def boundary(request, call_next):
        try:
            if request.url.path.startswith("/api/"):
                public = request.url.path in {
                    "/api/v1/setup/status",
                    "/api/v1/setup/administrator",
                    "/api/v1/session",
                }
                if request.method not in {"GET", "HEAD"}:
                    if request.headers.get("origin") != settings.origin:
                        raise Denied("Request origin was not accepted", 403)
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise Denied("Request is too large", 413)
                    request._body = bytes(body)
                if not public or (request.url.path == "/api/v1/session" and request.method != "POST"):
                    session = auth.session(request.cookies.get("campus_session"))
                    request.state.session = session
                    if request.method not in {"GET", "HEAD"}:
                        auth.csrf(session, request.headers.get("x-csrf-token"))
                if settings.demo_mode and request.method not in {"GET", "HEAD"}:
                    from backend.demo import permits_demo_action

                    if not permits_demo_action(request.method, request.url.path):
                        raise Denied(
                            "This demo supports draft editing only. Scanning, publication and live changes are disabled.",
                            403,
                        )
                response = await call_next(request)
            else:
                response = await call_next(request)
        except Denied as exc:
            response = JSONResponse({"code": "access_denied", "message": str(exc)}, status_code=exc.status)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                "Cache-Control": "no-store" if request.url.path.startswith("/api/") else "no-cache",
            }
        )
        return response

    def actor(request):
        return request.state.session["actor"]

    def mutation(request, body, operation):
        key = request.headers.get("idempotency-key", "")
        if not 16 <= len(key) <= 128:
            raise ValueError("An idempotency key is required for this action")
        with db.tx(write=True) as tx:
            return tx.once(
                key, actor(request), {"path": request.url.path, "body": body}, lambda: operation(tx)
            )

    def revision(request):
        try:
            return int(request.headers["if-match"].strip('"'))
        except (ValueError, KeyError):
            raise ValueError("Supply the current revision before changing this record") from None

    @app.get("/api/v1/setup/status")
    def setup_status():
        with db.tx() as tx:
            configured = bool(tx.c.execute("SELECT 1 FROM administrators LIMIT 1").fetchone())
        return {"configured": configured, "demo_mode": settings.demo_mode}

    @app.post("/api/v1/setup/administrator")
    def setup(body: Setup):
        return auth.setup(body.username, body.password, body.bootstrap_token)

    @app.post("/api/v1/session")
    def login(body: Login, request: Request, response: Response):
        token, csrf = auth.login(
            body.username, body.password, request.client.host if request.client else "unknown"
        )
        response.set_cookie(
            "campus_session",
            token,
            httponly=True,
            secure=settings.secure_cookie,
            samesite="strict",
            max_age=28800,
        )
        response.set_cookie(
            "campus_csrf",
            csrf,
            httponly=False,
            secure=settings.secure_cookie,
            samesite="strict",
            max_age=28800,
        )
        return {"actor": body.username}

    @app.get("/api/v1/session")
    def session(request: Request):
        return {"actor": actor(request), "expires": request.state.session["expires"].isoformat()}

    @app.delete("/api/v1/session")
    def logout(request: Request, response: Response):
        with db.tx(write=True) as tx:
            tx.c.execute("DELETE FROM sessions WHERE token_hash=%s", (request.state.session["token_hash"],))
            tx.audit(actor(request), "session.revoked", "login")
        response.delete_cookie("campus_session")
        response.delete_cookie("campus_csrf")
        return {"signed_out": True}

    @app.post("/api/v1/session/reauthenticate")
    @app.post("/api/v1/account/password")
    def password(body: PasswordConfirmation, request: Request):
        return auth.reauthenticate(request.state.session, body.password, body.new_password)

    @app.get("/api/v1/catalogue")
    def catalogue():
        return json.loads((ROOT / "backend/domain/catalogue.json").read_text())

    @app.get("/api/v1/overview")
    def overview():
        with db.tx() as tx:
            counts = tx.c.execute(
                "SELECT kind, count(*) AS count FROM records WHERE NOT (kind='device' AND data->>'status' IN ('merged','excluded')) GROUP BY kind"
            ).fetchall()
            statuses = tx.c.execute(
                "SELECT kind,data->>'status' AS status,count(*) AS count FROM records WHERE kind IN ('device','incident','check_result','execution','recommendation') GROUP BY kind,data->>'status'"
            ).fetchall()
            work = tx.list("discovery", 20) + tx.list("execution", 20) + tx.list("review", 20)
            system = [
                tx.get("system", key, False)
                for key in ["observation_worker", "inventory_worker", "execution_worker", "execution_pause"]
            ]
        return {
            "counts": {c["kind"]: c["count"] for c in counts},
            "statuses": statuses,
            "activity": sorted(work, key=lambda r: r["updated_at"], reverse=True)[:12],
            "runtime": [s for s in system if s],
            "at": now(),
        }

    @app.get("/api/v1/network")
    def network(limit: int = 250, after: str = ""):
        with db.tx() as tx:
            devices = [
                tx.unpack(row)
                for row in tx.c.execute(
                    "SELECT * FROM records WHERE kind='device' AND id>%s AND data->>'status' NOT IN ('merged','excluded') ORDER BY id LIMIT %s",
                    (after, min(max(limit, 1), 1000)),
                ).fetchall()
            ]
            edges = tx.list("edge", limit * 2)
        return {
            "devices": devices,
            "edges": edges,
            "next_cursor": devices[-1]["id"] if len(devices) == min(limit, 1000) else None,
        }

    @app.post("/api/v1/connectors")
    def connector(body: ConnectorRequest, request: Request):
        def save(tx):
            record = tx.put("connector", {**body.model_dump(), "status": "not_validated"})
            tx.audit(actor(request), "connector.created", record["id"], {"kind": body.kind})
            return record

        return mutation(request, body.model_dump(), save)

    @app.post("/api/v1/connectors/{key}/validate")
    def connector_validate(key: str, request: Request):
        def queue(tx):
            tx.get("connector", key)
            job = tx.put("connector_check", {"connector_id": key, "status": "queued"})
            tx.enqueue("connector_check", {"run_id": job["id"]}, job["id"])
            return job

        return mutation(request, {}, queue)

    @app.post("/api/v1/discovery-runs")
    def discover(body: DiscoveryRequest, request: Request):
        def start(tx):
            if body.connector_id:
                connector = tx.get("connector", body.connector_id)
                if connector["kind"] != "librenms":
                    raise ValueError("Choose a monitoring source for discovery")
            for profile_id in body.access_profile_ids:
                tx.get("access_profile", profile_id)
            return Discovery(db).create(tx, body.model_dump(), actor(request))

        return mutation(request, body.model_dump(), start)

    @app.post("/api/v1/access-profiles")
    def access_profile(body: AccessProfile, request: Request):
        auth.fresh(request.state.session)

        def add(tx):
            profile_id = uid()
            record = tx.put(
                "access_profile",
                {
                    **body.model_dump(),
                    "key_ref": body.key_ref or "ssh_" + profile_id.replace("-", ""),
                    "generate_key": body.key_ref is None,
                    "key_status": "queued",
                },
                profile_id,
            )
            tx.enqueue("access_key", {"profile_id": record["id"]}, record["id"])
            tx.audit(actor(request), "access_profile.created", record["id"])
            return record

        return mutation(request, body.model_dump(), add)

    @app.post("/api/v1/host-identities")
    def host_scan(body: TrustRequest, request: Request):
        def queue(tx):
            profile = tx.get("access_profile", body.profile_id)
            if profile.get("key_status") != "ready":
                raise Conflict("Wait for the access key to be prepared before collecting host identity")
            if not eligible(profile, body.address):
                raise ValueError("Host is outside the selected access profile")
            pending = tx.c.execute(
                "SELECT * FROM records WHERE kind='host_trust' AND data->>'profile_id'=%s "
                "AND data->>'address'=%s AND data->>'status' IN ('queued','scanning')",
                (body.profile_id, body.address),
            ).fetchone()
            if pending:
                return tx.unpack(pending)
            record = tx.put(
                "host_trust",
                {**body.model_dump(), "profile_revision": profile["revision"], "status": "queued"},
            )
            tx.enqueue("host_scan", {"trust_id": record["id"]}, record["id"])
            tx.audit(actor(request), "host_key.scan_requested", record["id"])
            return record

        return mutation(request, body.model_dump(), queue)

    @app.post("/api/v1/host-identities/{key}/approve")
    def approve_host(key: str, body: TrustApproval, request: Request):
        auth.fresh(request.state.session)

        def approve_identity(tx):
            record = tx.get("host_trust", key)
            profile = tx.get("access_profile", record["profile_id"])
            if (
                record["status"] != "awaiting_review"
                or not record.get("keys")
                or record.get("fingerprint_digest") != body.fingerprint_digest
                or digest(sorted(record["keys"])) != body.fingerprint_digest
                or record["profile_revision"] != profile["revision"]
                or datetime.fromisoformat(record["observed_at"]) < datetime.now(UTC) - timedelta(minutes=15)
            ):
                raise Conflict("Host identity changed or expired. Collect and review it again.")
            # Only one active trust record for this exact address/profile. Old evidence is retained.
            prior = tx.c.execute(
                "SELECT * FROM records WHERE kind='host_trust' AND data->>'profile_id'=%s "
                "AND data->>'address'=%s AND data->>'status'='approved'",
                (record["profile_id"], record["address"]),
            ).fetchall()
            for row in prior:
                old = tx.unpack(row)
                tx.put("host_trust", {**old, "status": "superseded"}, old["id"])
            result = tx.put(
                "host_trust",
                {
                    **record,
                    **body.model_dump(),
                    "status": "approved",
                    "identity_verified": body.basis == "independently_verified",
                    "approved_by": actor(request),
                    "approved_at": now(),
                },
                key,
                revision(request),
            )
            tx.audit(actor(request), "host_key.approved", key, {"basis": body.basis})
            return result

        return mutation(request, body.model_dump(), approve_identity)

    @app.post("/api/v1/access-holds/{key}/release")
    def release_hold(key: str, body: AccessRetry, request: Request):
        auth.fresh(request.state.session)

        def release(tx):
            hold = tx.get("access_hold", key)
            result = tx.put(
                "access_hold",
                {**hold, "released_at": now(), "release_reason": body.reason},
                key,
                revision(request),
            )
            tx.audit(actor(request), "access_hold.released", key, {"reason": body.reason})
            return result

        return mutation(request, body.model_dump(), release)

    @app.post("/api/v1/discovery-runs/{key}/cancel")
    def cancel_discovery(key: str, request: Request):
        def cancel(tx):
            record = tx.get("discovery", key)
            if record["status"] in {"queued", "running"}:
                record = tx.put("discovery", {**record, "status": "cancelled"}, key)
                tx.audit(actor(request), "discovery.cancelled", key)
            return record

        return mutation(request, {}, cancel)

    @app.post("/api/v1/devices")
    def add_device(body: DeviceEdit, request: Request):
        def add(tx):
            result = tx.put(
                "device",
                {
                    **body.model_dump(),
                    "status": "draft",
                    "access": "not_configured",
                    "source": "administrator",
                    "observed_at": None,
                },
            )
            tx.audit(actor(request), "device.added", result["id"], {"reason": body.reason})
            return result

        return mutation(request, body.model_dump(), add)

    @app.patch("/api/v1/devices/{key}")
    def edit_device(key: str, body: DeviceEdit, request: Request):
        def edit(tx):
            old = tx.get("device", key)
            result = tx.put("device", {**old, **body.model_dump(), "status": "draft"}, key, revision(request))
            tx.audit(
                actor(request),
                "device.corrected",
                key,
                {"previous_revision": old["revision"], "reason": body.reason},
            )
            return result

        return mutation(request, body.model_dump(), edit)

    @app.post("/api/v1/check-policies")
    def checks(body: CheckPolicy, request: Request):
        def add(tx):
            tx.get("device", body.target_id)
            tx.get("device", body.source_id)
            item = tx.put("check_policy", {**body.model_dump(), "next_due": now()})
            tx.audit(actor(request), "check_policy.created", item["id"])
            return item

        return mutation(request, body.model_dump(), add)

    @app.patch("/api/v1/check-policies/{key}")
    def edit_check(key: str, body: CheckPolicy, request: Request):
        def edit(tx):
            policy = tx.get("check_policy", key)
            tx.get("device", body.source_id)
            tx.get("device", body.target_id)
            result = tx.put(
                "check_policy", {**policy, **body.model_dump(), "approval_id": None}, key, revision(request)
            )
            tx.audit(actor(request), "check.updated", key)
            return result

        return mutation(request, body.model_dump(), edit)

    @app.post("/api/v1/probe-requests")
    def probe_request(body: ProbeRequest, request: Request):
        def queue(tx):
            source = tx.get("device", body.source_id)
            if source.get("status") != "accepted" or body.source_address not in source.get("addresses", []):
                raise Conflict("Choose an accepted source and one of its reviewed addresses")
            item = tx.put("probe_request", {**body.model_dump(), "status": "queued"})
            tx.put(
                "probe",
                {
                    "request_id": item["id"],
                    "source_address": body.source_address,
                    "verified_local": False,
                    "status": "queued",
                },
                body.source_id,
            )
            tx.enqueue("probe_verification", {"request_id": item["id"]}, item["id"])
            tx.audit(actor(request), "probe.requested", body.source_id)
            return item

        return mutation(request, body.model_dump(), queue)

    @app.post("/api/v1/check-policies/{key}/review")
    def review_check(key: str, request: Request):
        return mutation(request, {}, lambda tx: measurement_policies.prepare(tx, key, actor(request)))

    @app.post("/api/v1/check-reviews/{key}/approve")
    def approve_check(key: str, body: Approval, request: Request):
        auth.fresh(request.state.session)
        return mutation(
            request, body.model_dump(), lambda tx: measurement_policies.approve(tx, key, body, actor(request))
        )

    @app.post("/api/v1/check-policies/{key}/pause")
    def pause_check(key: str, request: Request):
        def pause(tx):
            policy = tx.get("check_policy", key)
            result = tx.put(
                "check_policy", {**policy, "enabled": False, "approval_id": None}, key, revision(request)
            )
            tx.audit(actor(request), "check.paused", key)
            return result

        return mutation(request, {}, pause)

    @app.post("/api/v1/check-policies/{key}/run")
    def run_check_now(key: str, request: Request):
        return mutation(
            request,
            {},
            lambda tx: measurement_policies.enqueue(tx, tx.get("check_policy", key), actor(request)),
        )

    @app.get("/api/v1/measurements")
    def measurements(limit: int = 50, after: str = ""):
        page_size = min(max(limit, 1), 200)
        with db.tx() as tx:
            policies = tx.list("check_policy", page_size + 1, after)
            more = len(policies) > page_size
            policies = policies[:page_size]
            items = []
            for policy in policies:
                result = tx.c.execute(
                    "SELECT * FROM records WHERE kind='check_result' AND data->>'policy_id'=%s ORDER BY created DESC,id DESC LIMIT 1",
                    (policy["id"],),
                ).fetchone()
                items.append(
                    {
                        **policy,
                        "source": tx.get("device", policy["source_id"]),
                        "target": tx.get("device", policy["target_id"]),
                        "probe": tx.get("probe", policy["source_id"], False),
                        "latest_result": tx.unpack(result) if result else None,
                        "active_run": tx.get("check_run", policy.get("active_run_id", ""), False),
                    }
                )
        return {"items": items, "next_cursor": policies[-1]["id"] if more else None}

    @app.get("/api/v1/check-policies/{key}/results")
    def check_history(key: str):
        with db.tx() as tx:
            tx.get("check_policy", key)
            rows = tx.c.execute(
                "SELECT * FROM records WHERE kind='check_result' AND data->>'policy_id'=%s ORDER BY created DESC,id DESC LIMIT 60",
                (key,),
            ).fetchall()
        return {"items": [tx.unpack(row) for row in rows]}

    @app.post("/api/v1/identity-merges")
    def merge_identities(body: MergeDevices, request: Request):
        return mutation(request, body.model_dump(), lambda tx: merge_devices(tx, body, actor(request)))

    @app.post("/api/v1/identity-splits")
    def split_identity(body: SplitDevice, request: Request):
        return mutation(request, body.model_dump(), lambda tx: split_device(tx, body, actor(request)))

    @app.post("/api/v1/devices/{key}/disposition")
    def identity_disposition(key: str, body: IdentityDisposition, request: Request):
        def update(tx):
            device = tx.get("device", key)
            if device["status"] == "merged":
                raise Conflict("Merged identities remain in history; review the surviving identity")
            result = tx.put(
                "device", {**device, **body.model_dump(), "access": "not_configured"}, key, revision(request)
            )
            for row in tx.c.execute(
                "SELECT * FROM records WHERE kind='check_policy' AND data->>'enabled'='true' "
                "AND (data->>'source_id'=%s OR data->>'target_id'=%s)",
                (key, key),
            ).fetchall():
                policy = tx.unpack(row)
                tx.put(
                    "check_policy",
                    {
                        **policy,
                        "enabled": False,
                        "approval_id": None,
                        "hold_reason": "A path identity was excluded or returned to draft inventory",
                    },
                    policy["id"],
                )
                tx.audit(actor(request), "check.paused_for_identity_review", policy["id"], {"device_id": key})
            tx.audit(actor(request), "device.disposition", key, body.model_dump())
            return result

        return mutation(request, body.model_dump(), update)

    @app.get("/api/v1/excluded-devices")
    def excluded_devices(limit: int = 100, after: str = ""):
        size = min(max(limit, 1), 1000)
        with db.tx() as tx:
            rows = tx.c.execute(
                "SELECT * FROM records WHERE kind='device' AND data->>'status'='excluded' AND id>%s ORDER BY id LIMIT %s",
                (after, size + 1),
            ).fetchall()
            items = [tx.unpack(row) for row in rows[:size]]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > size else None}

    @app.post("/api/v1/connections")
    def add_connection(body: EdgeEdit, request: Request):
        return mutation(request, body.model_dump(), lambda tx: save_edge(tx, body, actor(request)))

    @app.patch("/api/v1/connections/{key}")
    def edit_connection(key: str, body: EdgeEdit, request: Request):
        return mutation(
            request, body.model_dump(), lambda tx: save_edge(tx, body, actor(request), key, revision(request))
        )

    @app.post("/api/v1/baseline-drafts")
    def draft_inventory(body: BaselineDraft, request: Request):
        return mutation(request, body.model_dump(), lambda tx: prepare_baseline(tx, body, actor(request)))

    @app.post("/api/v1/baseline-drafts/{key}/approve")
    def accept_inventory(key: str, body: Approval, request: Request):
        auth.fresh(request.state.session)
        return mutation(
            request, body.model_dump(), lambda tx: approve_baseline(tx, key, body, actor(request))
        )

    @app.post("/api/v1/connectors/{key}/inventory-options")
    def refresh_inventory_options(key: str, request: Request):
        def queue(tx):
            connector = tx.get("connector", key)
            if connector["kind"] != "netbox":
                raise ValueError("Choose the intended-inventory connection")
            job = tx.put(
                "inventory_request",
                {
                    "connector_id": key,
                    "connector_revision": connector["revision"],
                    "operation": "options",
                    "status": "queued",
                },
            )
            tx.enqueue("inventory_options", {"request_id": job["id"]}, job["id"])
            return job

        return mutation(request, {}, queue)

    @app.post("/api/v1/connectors/{key}/inventory-objects")
    def inventory_object(key: str, body: InventoryObject, request: Request):
        def queue(tx):
            connector = tx.get("connector", key)
            if connector["kind"] != "netbox":
                raise ValueError("Choose the intended-inventory connection")
            job = tx.put(
                "inventory_request",
                {
                    **body.model_dump(),
                    "connector_id": key,
                    "connector_revision": connector["revision"],
                    "operation": "create_object",
                    "status": "queued",
                    "requested_by": actor(request),
                },
            )
            tx.enqueue("inventory_object", {"request_id": job["id"]}, job["id"])
            tx.audit(
                actor(request),
                "inventory_object.requested",
                job["id"],
                {"kind": body.kind, "reason": body.reason},
            )
            return job

        return mutation(request, body.model_dump(), queue)

    @app.post("/api/v1/configuration-reviews")
    def review(request: Request):
        def add(tx):
            item = tx.put("review_request", {"status": "queued"})
            tx.enqueue("configuration_review", {"request_id": item["id"]}, item["id"])
            return item

        return mutation(request, {}, add)

    @app.post("/api/v1/recommendations/{key}/disposition")
    def disposition(key: str, body: Disposition, request: Request):
        if body.status != "open":
            try:
                if datetime.fromisoformat(body.until or "") <= datetime.now(UTC):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("Choose a future review date") from None

        def update(tx):
            record = tx.get("recommendation", key)
            item = tx.put("recommendation", {**record, **body.model_dump()}, key, revision(request))
            tx.audit(actor(request), "recommendation.disposition", key, body.model_dump())
            return item

        return mutation(request, body.model_dump(), update)

    @app.post("/api/v1/repair-plans")
    @app.post("/api/v1/enrollment-plans")
    def plan(body: RepairRequest, request: Request):
        return mutation(request, body.model_dump(), lambda tx: compile_plan(tx, body, actor(request)))

    @app.post("/api/v1/repair-plans/{key}/approve-and-run")
    def approval(key: str, body: Approval, request: Request):
        auth.fresh(request.state.session)
        return mutation(
            request,
            body.model_dump(),
            lambda tx: approve(tx, key, body, actor(request), settings.mutations_enabled),
        )

    @app.post("/api/v1/executions/{key}/stop")
    def stop(key: str, request: Request):
        def change(tx):
            record = tx.get("execution", key)
            tx.audit(actor(request), "execution.stop_requested", key)
            return tx.put("execution", {**record, "stop_requested": True}, key)

        return mutation(request, {}, change)

    @app.post("/api/v1/execution-pause")
    @app.delete("/api/v1/execution-pause")
    def pause(request: Request):
        auth.fresh(request.state.session)

        def change(tx):
            item = tx.put("system", {"active": request.method == "POST"}, "execution_pause")
            tx.audit(actor(request), "execution.pause_changed", "installation", {"active": item["active"]})
            return item

        return mutation(request, {}, change)

    @app.get("/api/v1/access-coverage")
    def access():
        with db.tx() as tx:
            return {
                "devices": [
                    {"id": d["id"], "label": d["label"], "access": d.get("access", "not_configured")}
                    for d in tx.list("device", 1000)
                ],
                "enrollments": tx.list("enrollment", 1000),
            }

    @app.get("/api/v1/history")
    def history(after: int = 0):
        with db.tx() as tx:
            rows = tx.c.execute(
                "SELECT * FROM audit WHERE seq>%s ORDER BY seq LIMIT 100", (max(after, 0),)
            ).fetchall()
        return {"items": [{**r, "at": r["at"].isoformat()} for r in rows]}

    @app.get("/api/v1/ai-routes")
    def ai_routes():
        with db.tx() as tx:
            items = tx.list("ai_route", limit=1000)
            return {
                "items": [
                    {
                        **r,
                        "eligible": diagnosis_service.eligible(tx, r),
                        "health": tx.get("ai_gateway_health", r["content"]["connector_id"], False)
                        or tx.get("ai_route_health", r["id"], False),
                    }
                    for r in items
                ]
            }

    @app.post("/api/v1/ai-routes")
    def draft_ai_route(body: RouteDraft, request: Request):
        return mutation(
            request,
            body.model_dump(mode="json"),
            lambda tx: diagnosis_service.route_draft(tx, body, actor(request)),
        )

    @app.post("/api/v1/ai-routes/{key}/approve")
    def approve_ai_route(key: str, body: Approval, request: Request):
        auth.fresh(request.state.session)
        return mutation(
            request,
            body.model_dump(),
            lambda tx: diagnosis_service.approve_route(tx, key, body, actor(request)),
        )

    @app.post("/api/v1/ai-routes/{key}/pause")
    def pause_ai_route(key: str, request: Request):
        def pause(tx):
            route = tx.get("ai_route", key)
            result = tx.put("ai_route", {**route, "status": "paused"}, key, revision(request))
            tx.audit(actor(request), "ai.route_paused", key)
            return result

        return mutation(request, {}, pause)

    @app.post("/api/v1/diagnosis-previews")
    def preview_diagnosis(body: Subject, request: Request):
        return mutation(
            request,
            body.model_dump(),
            lambda tx: diagnosis_service.preview(tx, body.model_dump(), actor(request)),
        )

    @app.post("/api/v1/diagnoses/{key}/start")
    def start_diagnosis(key: str, body: Approval, request: Request):
        auth.fresh(request.state.session)
        return mutation(
            request, body.model_dump(), lambda tx: diagnosis_service.start(tx, key, body, actor(request))
        )

    @app.post("/api/v1/diagnoses/{key}/cancel")
    def cancel_diagnosis(key: str, request: Request):
        return mutation(request, {}, lambda tx: diagnosis_service.cancel(tx, key, actor(request)))

    @app.get("/api/v1/diagnosis-history/{kind}/{key}")
    def diagnosis_history(kind: str, key: str):
        if kind not in {"incident", "recommendation"}:
            raise ValueError("Choose an incident or configuration finding")
        with db.tx() as tx:
            rows = tx.c.execute(
                "SELECT * FROM records WHERE kind='diagnosis' AND data->'subject'->>'kind'=%s AND data->'subject'->>'id'=%s ORDER BY created DESC,id DESC LIMIT 20",
                (kind, key),
            ).fetchall()
            return {
                "items": [
                    {**(item := tx.unpack(row)), "current": diagnosis_service.still_current(tx, item)}
                    for row in rows
                ]
            }

    @app.get("/api/v1/installation/readiness")
    def readiness():
        with db.tx() as tx:
            workers = {
                key: tx.get("system", key, False)
                for key in ["observation_worker", "inventory_worker", "execution_worker"]
            }
            ai_routes_available = len(diagnosis_service.available_routes(tx))
            connectors = [
                {"id": c["id"], "name": c["name"], "status": c["status"]} for c in tx.list("connector")
            ]
        for worker in workers.values():
            if worker and worker.get("status") == "connected":
                try:
                    if (
                        not 0
                        <= (datetime.now(UTC) - datetime.fromisoformat(worker["at"])).total_seconds()
                        <= 30
                    ):
                        worker["status"] = "stale"
                except (ValueError, KeyError, TypeError):
                    worker["status"] = "unknown"
        return {
            "database": "available",
            "workers": workers,
            "connectors": connectors,
            "mutations_enabled": settings.mutations_enabled,
            "ai": f"{ai_routes_available} eligible reviewed routes"
            if ai_routes_available
            else "No eligible reviewed routes",
        }

    @app.get("/api/v1/events")
    async def events(request: Request, after: int = 0):
        async def stream():
            cursor = max(after, 0)
            for _ in range(120):
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.to_thread(auth.session, request.cookies.get("campus_session"))
                except Denied:
                    break

                def read(cursor=cursor):
                    with db.tx() as tx:
                        return tx.c.execute(
                            "SELECT seq,action,target FROM audit WHERE seq>%s ORDER BY seq LIMIT 100",
                            (cursor,),
                        ).fetchall()

                rows = await asyncio.to_thread(read)
                for row in rows:
                    cursor = row["seq"]
                    yield f"id: {cursor}\ndata: {canonical(row)}\n\n"
                if not rows:
                    yield ": keepalive\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/v1/{resource}")
    def list_resource(resource: str, limit: int = 100, after: str = ""):
        if resource not in PUBLIC_LISTS:
            raise Missing("Page not found")
        with db.tx() as tx:
            items = tx.list(PUBLIC_LISTS[resource], limit, after)
        return {"items": items, "next_cursor": items[-1]["id"] if len(items) == min(limit, 1000) else None}

    @app.get("/api/v1/{resource}/{key}")
    def get_resource(resource: str, key: str, response: Response):
        if resource not in PUBLIC_LISTS:
            raise Missing("Page not found")
        with db.tx() as tx:
            item = tx.get(PUBLIC_LISTS[resource], key)
        response.headers["ETag"] = f'"{item["revision"]}"'
        return item

    dist = ROOT / "ui/dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise Missing("API endpoint not found")
        if not (dist / "index.html").exists():
            return JSONResponse(
                {"message": "Build the operator interface before starting the server."}, status_code=503
            )
        return FileResponse(dist / "index.html")

    return app
