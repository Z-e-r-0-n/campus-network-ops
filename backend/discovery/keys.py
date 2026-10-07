"""Worker-owned keys. Public material only may enter records and activity results."""

import base64
import os
from hashlib import sha256
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from backend.config import private_read
from backend.db import now


def private_directory(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError("Key storage directory must be private and owned by the worker")


def prepare_profile_key(store, settings, profile_id):
    with store.tx() as tx:
        profile = tx.get("access_profile", profile_id)
    if profile.get("key_status") == "ready":
        return {"id": profile_id, "status": "ready"}
    try:
        directory = settings.state / "secrets"
        private_directory(directory)
        path = directory / profile["key_ref"]
        if profile["generate_key"] and not path.exists():
            key = (
                ed25519.Ed25519PrivateKey.generate()
                if profile["key_algorithm"] == "ed25519"
                else rsa.generate_private_key(public_exponent=65537, key_size=3072)
            )
            material = key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
            )
            # Publish a complete file atomically without replacing any pre-existing key.
            temporary = directory / ("." + profile["key_ref"] + "." + os.urandom(8).hex())
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(material)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path, follow_symlinks=False)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        key = serialization.load_ssh_private_key(private_read(path).encode(), password=None)
        if not (
            isinstance(key, ed25519.Ed25519PrivateKey)
            or isinstance(key, rsa.RSAPrivateKey)
            and key.key_size >= 3072
        ):
            raise ValueError("Unsupported private key")
        public = (
            key.public_key()
            .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
            .decode()
        )
        fingerprint = "SHA256:" + base64.b64encode(
            sha256(base64.b64decode(public.split()[1])).digest()
        ).decode().rstrip("=")
        result = {
            "key_status": "ready",
            "public_key": public,
            "fingerprint": fingerprint,
            "key_prepared_at": now(),
        }
    except (ValueError, TypeError, OSError):
        result = {"key_status": "unavailable", "key_detail": "Key storage or key format needs local review"}
    with store.tx(write=True) as tx:
        current = tx.get("access_profile", profile_id)
        if current.get("key_status") == "ready":
            return {"id": profile_id, "status": "ready"}
        tx.put("access_profile", {**current, **result}, profile_id)
        tx.audit("access", "access_key.prepared", profile_id, {"status": result["key_status"]})
    return {"id": profile_id, "status": result["key_status"]}
