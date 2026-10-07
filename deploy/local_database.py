#!/usr/bin/env python3
"""Isolated PostgreSQL for development/acceptance; never opens the old installation."""

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"
BIN = Path("/usr/lib/postgresql/18/bin")


def run(args):
    subprocess.run([str(x) for x in args], check=True, stdout=subprocess.DEVNULL)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["start", "stop"])
    args = p.parse_args()
    os.umask(0o077)
    for path in [STATE, STATE / "secrets", STATE / "socket"]:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = STATE / "postgres"
    if args.action == "stop":
        run([BIN / "pg_ctl", "-D", data, "stop", "-m", "fast"])
        return
    if not (data / "PG_VERSION").exists():
        run(
            [
                BIN / "initdb",
                "-D",
                data,
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
    status = subprocess.run([BIN / "pg_ctl", "-D", data, "status"], stdout=subprocess.DEVNULL)
    if status.returncode:
        # The protected Unix socket is the only listener; no host TCP authentication bypass.
        run(
            [
                BIN / "pg_ctl",
                "-D",
                data,
                "-l",
                STATE / "postgres.log",
                "-o",
                f"-k '{STATE / 'socket'}' -h '' -p 55442",
                "start",
            ]
        )
    import psycopg

    dsn = f"host='{STATE / 'socket'}' port=55442 dbname=postgres"
    with psycopg.connect(dsn, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname='campus_next'").fetchone():
            c.execute("CREATE DATABASE campus_next")
    target = STATE / "secrets/database_url"
    target.write_text(f"host='{STATE / 'socket'}' port=55442 dbname=campus_next\n")
    target.chmod(0o600)
    print("Isolated PostgreSQL ready; existing databases unchanged.")


if __name__ == "__main__":
    main()
