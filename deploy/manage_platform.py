#!/usr/bin/env python3
"""Repeatable local dependency setup. Never restores old inventory or schedules."""

import argparse
import os
import secrets
import string
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import private_read  # noqa: E402


def provision(directory, name, value):
    path = directory / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return private_read(path)
    with os.fdopen(fd, "w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "up", "status", "bootstrap-inventory"])
    args = parser.parse_args()
    directory = ROOT / ".state/secrets"
    os.umask(0o077)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise SystemExit("Protect the installation secrets directory first")
    if args.action in {"prepare", "up"}:
        for name in [
            "temporal_password",
            "netbox_database_password",
            "netbox_cache_password",
            "netbox_secret_key",
            "netbox_api_pepper",
        ]:
            provision(directory, name, secrets.token_urlsafe(48))
        chars = string.ascii_letters + string.digits
        token = (
            "nbt_"
            + "".join(secrets.choice(chars) for _ in range(12))
            + "."
            + "".join(secrets.choice(chars) for _ in range(40))
        )
        provision(directory, "netbox_token", token)
        password = private_read(directory / "netbox_cache_password")
        provision(
            directory,
            "valkey_config",
            f"bind 0.0.0.0\nprotected-mode yes\nrequirepass {password}\nappendonly yes\ndir /data\n",
        )
    if args.action == "prepare":
        print("Private dependency credentials prepared; existing values preserved.")
        return
    command = ["docker", "compose", "-f", str(ROOT / "deploy/compose.yaml")]
    env = {**os.environ, "CAMPUS_NEXT_UID": str(os.getuid())}
    if args.action == "up":
        subprocess.run(
            command
            + [
                "run",
                "--rm",
                "--no-deps",
                "--user",
                "0:0",
                "--entrypoint",
                "/bin/sh",
                "netbox-cache",
                "-c",
                f"chown {os.getuid()}:0 /data",
            ],
            env=env,
            check=True,
        )
        subprocess.run(command + ["up", "-d"], env=env, check=True)
    elif args.action == "status":
        subprocess.run(command + ["ps"], env=env, check=True)
    elif args.action == "bootstrap-inventory":
        script = (ROOT / "deploy/netbox_bootstrap.py").read_text()
        result = subprocess.run(
            command
            + [
                "exec",
                "-T",
                "netbox",
                "/opt/netbox/venv/bin/python",
                "/opt/netbox/netbox/manage.py",
                "shell",
                "--no-startup",
                "--no-imports",
                "--interface",
                "python",
            ],
            input=script,
            text=True,
            capture_output=True,
            env=env,
        )
        if result.returncode:
            # A traceback may include source values. Keep the operator-facing error bounded.
            path = ROOT / ".state/netbox-bootstrap.log"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(result.stdout + result.stderr)
            raise SystemExit(
                "Inventory identity bootstrap failed; diagnostics are in private .state/netbox-bootstrap.log"
            )
        print("Restricted inventory identity configured. No campus records imported.")
        from backend.config import Settings
        from backend.connectors.netbox import NetBox
        from backend.db import Store, now
        from backend.domain.models import ConnectorRequest

        settings = Settings(state=ROOT / ".state")
        specification = ConnectorRequest(
            name="Reviewed inventory",
            kind="netbox",
            endpoint="http://127.0.0.1:8876/api",
            pinned_address="127.0.0.1",
            secret_ref="netbox_token",
            source_timezone="UTC",
            source_timezone_verified=True,
        ).model_dump()
        options = NetBox(specification, settings.secret("netbox_token")).options()
        with Store(settings.dsn).tx(write=True) as tx:
            existing = tx.get("connector", "managed_inventory", False)
            if existing is None:
                tx.put(
                    "connector",
                    {**specification, "status": "connected", "last_checked_at": now()},
                    "managed_inventory",
                )
                tx.put(
                    "inventory_options",
                    {"connector_id": "managed_inventory", "options": options, "collected_at": now()},
                    "managed_inventory",
                )
                tx.audit("installer", "connector.created", "managed_inventory", {"kind": "netbox"})
        print("Fresh intended-inventory connection registered; existing operator configuration preserved.")


if __name__ == "__main__":
    main()
