#!/usr/bin/env python3
"""Launch an isolated interactive preview. No workers or campus credentials are loaded."""

import os
import secrets
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import psycopg
import uvicorn
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.api.app import create_app  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.db import Store  # noqa: E402
from backend.demo import DEMO_USERNAME, seed_demo  # noqa: E402
from backend.security.auth import Auth, hashed  # noqa: E402


def main():
    os.umask(0o077)
    dsn = Settings(state=ROOT / ".state").dsn
    schema = "demo_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        db = Store(make_conninfo(dsn, options=f"-c search_path={schema}"))
        db.migrate()
        token = secrets.token_urlsafe(32)
        demo_password = secrets.token_urlsafe(24)
        with db.tx(write=True) as tx:
            tx.put(
                "system",
                {
                    "token_hash": hashed(token),
                    "expires": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                },
                "bootstrap",
            )
        Auth(db).setup(DEMO_USERNAME, demo_password, token)
        seed_demo(db)
        with TemporaryDirectory(prefix="campus-next-demo-", dir="/tmp") as state:
            settings = Settings(
                state=Path(state),
                origin="http://localhost:8885",
                demo_mode=True,
                mutations_enabled=False,
            )
            # Uvicorn restores and re-raises SIGTERM after graceful shutdown; ignoring its
            # re-raise allows this launcher's finally block to remove only its own schema.
            signal.signal(signal.SIGTERM, lambda *_: None)
            print("Interactive sample demo: http://localhost:8885/#Network", flush=True)
            print(f"Demo username: {DEMO_USERNAME}", flush=True)
            print(f"Demo password: {demo_password}", flush=True)
            uvicorn.run(
                create_app(settings, db), host="127.0.0.1", port=8885, access_log=False, log_level="warning"
            )
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    main()
