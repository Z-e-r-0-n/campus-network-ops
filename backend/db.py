"""Transactional records, revisions, immutable audit and durable work dispatch."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS migrations(version integer PRIMARY KEY, applied timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS records(
 kind text NOT NULL, id text NOT NULL, revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
 data jsonb NOT NULL CHECK(jsonb_typeof(data)='object'),
 created timestamptz NOT NULL DEFAULT now(), updated timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(kind,id));
CREATE INDEX IF NOT EXISTS records_updated ON records(kind, updated DESC, id);
CREATE INDEX IF NOT EXISTS check_results_policy ON records((data->>'policy_id'), created DESC, id DESC)
 WHERE kind='check_result';
CREATE TABLE IF NOT EXISTS audit(
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL,
 actor text NOT NULL, action text NOT NULL, target text NOT NULL,
 detail jsonb NOT NULL, previous text NOT NULL, digest text NOT NULL);
CREATE TABLE IF NOT EXISTS administrators(name text PRIMARY KEY, password_hash text NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
 token_hash text PRIMARY KEY, actor text NOT NULL REFERENCES administrators(name),
 csrf_hash text NOT NULL, expires timestamptz NOT NULL, authenticated timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS login_limits(key text PRIMARY KEY, failures integer NOT NULL, reset_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency(key text PRIMARY KEY, actor text NOT NULL, fingerprint text NOT NULL, response jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(
 id text PRIMARY KEY, kind text NOT NULL, payload jsonb NOT NULL,
 created timestamptz NOT NULL DEFAULT now(), dispatched timestamptz);
CREATE TABLE IF NOT EXISTS device_leases(
 device text PRIMARY KEY, owner text NOT NULL, fence bigint NOT NULL, until_at timestamptz NOT NULL);
INSERT INTO migrations(version) VALUES(1) ON CONFLICT DO NOTHING;
"""


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return str(uuid4())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


class Store:
    def __init__(self, dsn):
        self.dsn = dsn

    def migrate(self):
        with self.tx(write=True) as tx:
            tx.c.execute(SCHEMA)

    @contextmanager
    def tx(self, write=False):
        with psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row) as c:
            c.execute("SET LOCAL statement_timeout = '15s'")
            c.execute("SET LOCAL lock_timeout = '10s'")
            if write:
                # Serializes short metadata transactions; no network calls under this lock.
                c.execute("SELECT pg_advisory_xact_lock(9282026001)")
            yield Transaction(c)


class Transaction:
    def __init__(self, c):
        self.c = c

    def get(self, kind, key, required=True):
        row = self.c.execute("SELECT * FROM records WHERE kind=%s AND id=%s", (kind, key)).fetchone()
        if not row:
            if required:
                raise Missing(f"{kind} record not found")
            return None
        return self.unpack(row)

    @staticmethod
    def unpack(row):
        return {
            **row["data"],
            "id": row["id"],
            "revision": row["revision"],
            "created_at": row["created"].isoformat(),
            "updated_at": row["updated"].isoformat(),
        }

    def list(self, kind, limit=100, after=""):
        rows = self.c.execute(
            "SELECT * FROM records WHERE kind=%s AND id>%s ORDER BY id LIMIT %s",
            (kind, after, min(max(limit, 1), 1000)),
        ).fetchall()
        return [self.unpack(row) for row in rows]

    def put(self, kind, data, key=None, expected=None):
        key = key or uid()
        old = self.get(kind, key, False)
        if expected is not None and (not old or old["revision"] != expected):
            raise Conflict("This record changed. Refresh it before saving.")
        payload = {k: v for k, v in data.items() if k not in {"id", "revision", "created_at", "updated_at"}}
        self.c.execute(
            """INSERT INTO records(kind,id,data) VALUES(%s,%s,%s)
            ON CONFLICT(kind,id) DO UPDATE SET data=EXCLUDED.data, revision=records.revision+1, updated=now()""",
            (kind, key, Jsonb(payload)),
        )
        return self.get(kind, key)

    def audit(self, actor, action, target, detail=None):
        previous = self.c.execute("SELECT digest FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        previous = previous["digest"] if previous else "0" * 64
        at = now()
        body = {
            "at": at,
            "actor": actor,
            "action": action,
            "target": target,
            "detail": detail or {},
            "previous": previous,
        }
        self.c.execute(
            "INSERT INTO audit(at,actor,action,target,detail,previous,digest) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (at, actor, action, target, Jsonb(body["detail"]), previous, digest(body)),
        )

    def enqueue(self, kind, payload, key=None):
        key = key or uid()
        self.c.execute(
            "INSERT INTO outbox(id,kind,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (key, kind, Jsonb(payload)),
        )
        return key

    def once(self, key, actor, body, operation):
        fingerprint = digest(body)
        prior = self.c.execute("SELECT * FROM idempotency WHERE key=%s", (key,)).fetchone()
        if prior:
            if prior["fingerprint"] != fingerprint or prior["actor"] != actor:
                raise Conflict("Idempotency key was used for a different request")
            return prior["response"]
        result = operation()
        self.c.execute(
            "INSERT INTO idempotency VALUES(%s,%s,%s,%s)", (key, actor, fingerprint, Jsonb(result))
        )
        return result
