import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from backend.db import Conflict

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))


class Denied(Exception):
    def __init__(self, message="Authentication required", status=401):
        self.status = status
        super().__init__(message)


def hashed(token):
    return sha256(token.encode()).hexdigest()


def validate_password(password):
    if len(password) < 14 or len(password) > 256:
        raise ValueError("Choose a password between 14 and 256 characters")


class Auth:
    def __init__(self, store):
        self.store = store

    def setup(self, username, password, bootstrap_token):
        validate_password(password)
        if not username or len(username) > 64 or not all(c.isalnum() or c in "_.-" for c in username):
            raise ValueError("Use letters, numbers, dots, underscores or hyphens for the username")
        password_hash = HASHER.hash(password)
        with self.store.tx(write=True) as tx:
            if tx.c.execute("SELECT 1 FROM administrators LIMIT 1").fetchone():
                raise Conflict("Administrator is already configured")
            setup = tx.get("system", "bootstrap", False)
            if not setup or datetime.fromisoformat(setup["expires"]) <= datetime.now(UTC):
                raise Denied("Setup access expired. Renew it using the local setup command.", 403)
            if not secrets.compare_digest(setup["token_hash"], hashed(bootstrap_token)):
                raise Denied("Setup access was not accepted", 403)
            tx.c.execute("INSERT INTO administrators VALUES(%s,%s)", (username, password_hash))
            tx.put("system", {"consumed": True}, "bootstrap")
            tx.audit(username, "administrator.created", "installation")
        return {"configured": True}

    def login(self, username, password, source):
        instant = datetime.now(UTC)
        limit_key = hashed(source)
        with self.store.tx(write=True) as tx:
            row = tx.c.execute("SELECT * FROM login_limits WHERE key=%s", (limit_key,)).fetchone()
            if row and row["reset_at"] > instant and row["failures"] >= 8:
                raise Denied("Too many attempts. Try again in 15 minutes.", 429)
            # Reserve before expensive verification; concurrent attempts cannot bypass the limit.
            tx.c.execute(
                """INSERT INTO login_limits VALUES(%s,1,%s) ON CONFLICT(key) DO UPDATE SET
             failures=CASE WHEN login_limits.reset_at<=now() THEN 1 ELSE login_limits.failures+1 END,
             reset_at=CASE WHEN login_limits.reset_at<=now() THEN EXCLUDED.reset_at ELSE login_limits.reset_at END""",
                (limit_key, instant + timedelta(minutes=15)),
            )
            user = tx.c.execute("SELECT * FROM administrators WHERE name=%s", (username,)).fetchone()
        try:
            HASHER.verify(user["password_hash"] if user else DUMMY_HASH, password)
            valid = user is not None
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid:
            with self.store.tx(write=True) as tx:
                tx.audit("anonymous", "session.rejected", "login")
            raise Denied("Username or password was not accepted")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.tx(write=True) as tx:
            # Password changes during verification invalidate this login.
            current = tx.c.execute(
                "SELECT password_hash FROM administrators WHERE name=%s", (username,)
            ).fetchone()
            if current["password_hash"] != user["password_hash"]:
                raise Denied("Account changed. Sign in again.")
            tx.c.execute("DELETE FROM login_limits WHERE key=%s", (limit_key,))
            tx.c.execute(
                "INSERT INTO sessions VALUES(%s,%s,%s,%s,%s)",
                (hashed(token), username, hashed(csrf), instant + timedelta(hours=8), instant),
            )
            tx.audit(username, "session.created", "login")
        return token, csrf

    def session(self, token):
        with self.store.tx() as tx:
            row = tx.c.execute(
                "SELECT * FROM sessions WHERE token_hash=%s AND expires>now()", (hashed(token or ""),)
            ).fetchone()
        if not row:
            raise Denied()
        return row

    @staticmethod
    def csrf(session, supplied):
        if not secrets.compare_digest(session["csrf_hash"], hashed(supplied or "")):
            raise Denied("This form expired. Refresh the page.", 403)

    @staticmethod
    def fresh(session):
        if session["authenticated"] < datetime.now(UTC) - timedelta(minutes=10):
            raise Denied("Confirm your password before approving a change.", 403)

    def reauthenticate(self, session, password, new_password=None):
        with self.store.tx() as tx:
            user = tx.c.execute("SELECT * FROM administrators WHERE name=%s", (session["actor"],)).fetchone()
        try:
            HASHER.verify(user["password_hash"], password)
        except (VerificationError, InvalidHashError):
            raise Denied("Password was not accepted") from None
        if new_password is not None:
            validate_password(new_password)
            replacement = HASHER.hash(new_password)
        with self.store.tx(write=True) as tx:
            if new_password is not None:
                tx.c.execute(
                    "UPDATE administrators SET password_hash=%s WHERE name=%s",
                    (replacement, session["actor"]),
                )
                tx.c.execute(
                    "DELETE FROM sessions WHERE actor=%s AND token_hash<>%s",
                    (session["actor"], session["token_hash"]),
                )
            tx.c.execute(
                "UPDATE sessions SET authenticated=now() WHERE token_hash=%s", (session["token_hash"],)
            )
            tx.audit(
                session["actor"],
                "account.password_changed" if new_password else "session.reauthenticated",
                "account",
            )
        return {"confirmed": True}
