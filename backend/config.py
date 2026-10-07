import os
from dataclasses import dataclass, field
from pathlib import Path


def private_read(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("Credential file must be a private regular file")
    if path.stat().st_uid != os.getuid():
        raise ValueError("Credential file owner differs from service identity")
    return path.read_text().strip()


@dataclass(frozen=True)
class Settings:
    state: Path = field(default_factory=lambda: Path(os.getenv("CAMPUS_NEXT_STATE", ".state")).resolve())
    origin: str = field(default_factory=lambda: os.getenv("CAMPUS_NEXT_ORIGIN", "http://127.0.0.1:8875"))
    temporal: str = field(default_factory=lambda: os.getenv("CAMPUS_NEXT_TEMPORAL", "127.0.0.1:7333"))
    namespace: str = "campus-next"
    opa: str = field(default_factory=lambda: os.getenv("CAMPUS_NEXT_OPA", "http://127.0.0.1:8281"))
    mutations_enabled: bool = field(default_factory=lambda: os.getenv("CAMPUS_NEXT_MUTATIONS") == "enabled")
    # Set only by the isolated demonstration launcher, never by a deployment environment variable.
    demo_mode: bool = False

    @property
    def dsn(self):
        return private_read(self.state / "secrets/database_url")

    @property
    def secure_cookie(self):
        return self.origin.startswith("https://")

    def secret(self, reference: str) -> str:
        # The database stores names, never arbitrary filesystem paths.
        if not reference or not all(c.isalnum() or c in "_-" for c in reference):
            raise ValueError("Invalid credential reference")
        return private_read(self.state / "secrets" / reference)
