"""Opt-in contract acceptance against the installed image and a disposable DB.

RUN_NETBOX_CONTRACT=1 .venv/bin/pytest -q tests/test_netbox_contract.py
Never writes objects to the installation's NetBox database.
"""

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from backend.config import Settings
from backend.connectors.http import ConnectorUnavailable
from backend.connectors.netbox import NetBox

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NETBOX_CONTRACT") != "1", reason="Opt-in isolated NetBox contract test"
)


def command(args, data=None):
    __tracebackhide__ = True
    result = subprocess.run(args, input=data, capture_output=True, timeout=120)
    if result.returncode:
        path = Path(__file__).resolve().parents[1] / ".state/acceptance-netbox-diagnostics.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(result.stderr)
        raise RuntimeError("Isolated NetBox contract command failed (output withheld to protect credentials)")
    return result.stdout


@pytest.fixture
def inventory():
    __tracebackhide__ = True
    root = Path(__file__).resolve().parents[1]
    settings = Settings(state=root / ".state")
    suffix = uuid4().hex[:12]
    database, container = "contract_" + suffix, "campus-next-contract-" + suffix
    db_command = ["docker", "exec", "-i", "campus-next-netbox-db-1"]
    made_database = False
    try:
        # Start with an empty database, then run the normal installation migrations.
        # No production objects or identity assumptions are copied into the fixture.
        command(db_command + ["createdb", "-U", "netbox", database])
        made_database = True
        args = [
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--user",
            f"{os.getuid()}:0",
            "--network",
            "campus-next_backplane",
            "--network",
            "campus-next_management",
            "--publish",
            "127.0.0.1:18876:8080",
            "--memory",
            "2g",
            "--security-opt",
            "no-new-privileges:true",
        ]
        env = {
            "DB_HOST": "netbox-db",
            "DB_USER": "netbox",
            "DB_NAME": database,
            "REDIS_HOST": "netbox-cache",
            "REDIS_CACHE_HOST": "netbox-cache",
            "REDIS_DATABASE": "2",
            "REDIS_CACHE_DATABASE": "3",
            "ALLOWED_HOSTS": "127.0.0.1 localhost",
            "SKIP_SUPERUSER": "true",
            "CENSUS_REPORTING_ENABLED": "false",
            "ISOLATED_DEPLOYMENT": "true",
            "COPILOT_ENABLED": "false",
            "WEBHOOKS_ENABLED": "false",
        }
        for name, value in env.items():
            args += ["--env", name + "=" + value]
        for source, target in [
            ("netbox_database_password", "db_password"),
            ("netbox_cache_password", "redis_password"),
            ("netbox_cache_password", "redis_cache_password"),
            ("netbox_secret_key", "secret_key"),
            ("netbox_api_pepper", "api_token_pepper_1"),
            ("netbox_token", "campus_token"),
        ]:
            args += [
                "--mount",
                f"type=bind,source={settings.state / 'secrets' / source},target=/run/secrets/{target},readonly",
            ]
        args += ["netboxcommunity/netbox:v4.7-5.1.1"]
        command(args)
        client = NetBox(
            {"endpoint": "http://127.0.0.1:18876/api", "pinned_address": "127.0.0.1"},
            settings.secret("netbox_token"),
        )
        for _ in range(300):
            try:
                with httpx.Client(trust_env=False, timeout=1) as http:
                    if http.get("http://127.0.0.1:18876/login/").status_code == 200:
                        break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Isolated NetBox did not become ready")
        command(
            [
                "docker",
                "exec",
                "-i",
                container,
                "/opt/netbox/venv/bin/python",
                "/opt/netbox/netbox/manage.py",
                "shell",
                "--no-startup",
                "--no-imports",
                "--interface",
                "python",
            ],
            (root / "deploy/netbox_bootstrap.py").read_bytes(),
        )
        client.options()
        yield client, suffix
    finally:
        logs = subprocess.run(["docker", "logs", "--tail", "100", container], capture_output=True, timeout=10)
        path = root / ".state/acceptance-netbox-container.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(logs.stdout + logs.stderr)
        subprocess.run(["docker", "rm", "--force", container], capture_output=True, timeout=30)
        if made_database:
            command(db_command + ["dropdb", "-U", "netbox", "--force", database])


def test_reviewed_device_cable_and_conditional_update(inventory):
    client, suffix = inventory

    def create(kind, **fields):
        return client.create_object({"kind": kind, "name": "Contract " + kind + " " + suffix, **fields})["id"]

    site = create("sites")
    manufacturer = create("manufacturers")
    model = create("device-types", manufacturer_id=manufacturer)
    role = create("device-roles")
    mapping = {"site": site, "device_type": model, "role": role, "status": "active"}
    a = {"id": "test-a-" + suffix, "label": "Contract A " + suffix}
    b = {"id": "test-b-" + suffix, "label": "Contract B " + suffix}
    results = {d["id"]: client.publish_device(d, mapping, "contract-baseline") for d in [a, b]}
    old = results[a["id"]]
    assert old["etag"], "NetBox must expose an ETag for safe conditional writes"
    updated = client.publish_device(
        a,
        {
            **mapping,
            "status": "offline",
            "external_id": old["external_id"],
            "expected_etag": old["etag"],
            "expected_last_updated": old["last_updated"],
        },
        "contract-baseline-2",
    )
    assert updated["etag"] != old["etag"]
    with pytest.raises(ConnectorUnavailable):
        client.http.request(
            "PATCH",
            f"/dcim/devices/{old['external_id']}/",
            {"status": "active"},
            headers={"If-Match": old["etag"]},
        )
    edge = {
        "id": "edge-" + suffix,
        "layer": "confirmed_physical",
        "source_id": a["id"],
        "target_id": b["id"],
        "local_port": "port-1",
        "remote_port": "port-2",
    }
    cable = client.publish_connection(edge, results)
    assert cable["representation"] == "netbox_cable"
    assert client.publish_connection(edge, results) == cable
