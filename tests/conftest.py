from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.api.app import create_app
from backend.config import Settings
from backend.db import Store


@pytest.fixture
def db():
    root = Path(__file__).resolve().parents[1]
    path = root / ".state/secrets/database_url"
    if not path.exists():
        pytest.fail("Start the isolated PostgreSQL test database first")
    dsn = path.read_text().strip()
    schema = "acceptance_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    store = Store(make_conninfo(dsn, options=f"-c search_path={schema}"))
    store.migrate()
    yield store
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def client(db, tmp_path):
    settings = Settings(state=tmp_path, origin="http://testserver")
    with TestClient(create_app(settings, db)) as client:
        client.headers["Origin"] = "http://testserver"
        yield client
