#!/usr/bin/env python3
"""Local administrative operations; credentials are never printed."""

import argparse
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.config import Settings
from backend.db import Store
from backend.security.auth import hashed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "setup-access", "status"])
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parent)
    os.umask(0o077)
    config = Settings()
    db = Store(config.dsn)
    db.migrate()
    if args.action == "setup-access":
        with db.tx(write=True) as tx:
            if tx.c.execute("SELECT 1 FROM administrators LIMIT 1").fetchone():
                raise SystemExit("Administrator already exists; setup access was not changed.")
            token = secrets.token_urlsafe(32)
            path = config.state / "secrets/setup_token"
            path.write_text(token)
            path.chmod(0o600)
            tx.put(
                "system",
                {
                    "token_hash": hashed(token),
                    "expires": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                },
                "bootstrap",
            )
        print("Setup access prepared in the private setup_token file; valid for 24 hours.")
    elif args.action == "status":
        with db.tx() as tx:
            configured = bool(tx.c.execute("SELECT 1 FROM administrators LIMIT 1").fetchone())
            count = tx.c.execute("SELECT count(*) AS n FROM records").fetchone()["n"]
        print(
            {
                "database": "reachable",
                "administrator_configured": configured,
                "records": count,
                "campus_mutations_enabled": config.mutations_enabled,
            }
        )
    else:
        print("Database migrations applied.")


if __name__ == "__main__":
    main()
