from ipaddress import ip_address, ip_network
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Scope(Model):
    prefixes: list[str] = Field(min_length=1, max_length=64)
    max_nodes: int = Field(default=500, ge=1, le=10000)
    max_depth: int = Field(default=12, ge=0, le=64)

    @field_validator("prefixes")
    @classmethod
    def networks(cls, values):
        return [str(ip_network(value, strict=True)) for value in values]

    def permits(self, address):
        try:
            addr = ip_address(address)
            return (
                not addr.is_multicast
                and not addr.is_unspecified
                and any(addr in ip_network(prefix) for prefix in self.prefixes)
            )
        except ValueError:
            return False


class DiscoveryRequest(Model):
    seeds: list[str] = Field(min_length=1, max_length=32)
    scope: Scope
    connector_id: str | None = None
    access_profile_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("seeds")
    @classmethod
    def addresses(cls, values):
        return list(dict.fromkeys(str(ip_address(value)) for value in values))

    @model_validator(mode="after")
    def check_scope(self):
        if not all(self.scope.permits(seed) for seed in self.seeds):
            raise ValueError("Every seed must be inside the discovery scope")
        return self


class ConnectorRequest(Model):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["librenms", "netbox", "omniroute", "oxidized"]
    endpoint: str = Field(max_length=512)
    pinned_address: str
    secret_ref: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    source_timezone: str = "UTC"
    source_timezone_verified: bool = False
    allow_private_http: bool = False

    @field_validator("source_timezone")
    @classmethod
    def valid_timezone(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Choose a valid source timezone") from None
        return value

    @field_validator("pinned_address")
    @classmethod
    def ip(cls, value):
        addr = ip_address(value)
        if addr.is_multicast or addr.is_unspecified:
            raise ValueError("Choose a specific unicast address")
        return str(addr)

    @field_validator("endpoint")
    @classmethod
    def endpoint_valid(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Use an HTTP(S) endpoint without embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Connector endpoint cannot contain query parameters or fragments")
        return value.rstrip("/")

    @model_validator(mode="after")
    def transport_policy(self):
        address = ip_address(self.pinned_address)
        if urlsplit(self.endpoint).scheme == "http" and not address.is_loopback:
            if not self.allow_private_http or not address.is_private:
                raise ValueError("HTTP requires an explicitly accepted private-network compatibility setting")
        return self


class AccessProfile(Model):
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
    key_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    key_algorithm: Literal["ed25519", "rsa3072"] = "ed25519"
    prefixes: list[str] = Field(min_length=1, max_length=64)
    platform: Literal["auto", "cisco_iosxe", "dlink", "linux", "freebsd"] = "auto"
    port: int = Field(default=22, ge=1, le=65535)

    @field_validator("prefixes")
    @classmethod
    def networks(cls, values):
        return [str(ip_network(value, strict=True)) for value in values]


class TrustRequest(Model):
    profile_id: str
    address: str

    @field_validator("address")
    @classmethod
    def address_valid(cls, value):
        return str(ip_address(value))


class TrustApproval(Model):
    fingerprint_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    basis: Literal["independently_verified", "accepted_first_use"]
    note: str = Field(min_length=3, max_length=300)


class AccessRetry(Model):
    reason: str = Field(min_length=3, max_length=300)


class DeviceEdit(Model):
    label: str = Field(min_length=1, max_length=120)
    role: Literal[
        "unknown", "core", "distribution", "access", "router", "firewall", "server", "probe", "unmanaged"
    ]
    addresses: list[str] = Field(default_factory=list, max_length=32)
    location: str = Field(default="", max_length=120)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("addresses")
    @classmethod
    def addresses_valid(cls, values):
        return [str(ip_address(v)) for v in values]


class Disposition(Model):
    status: Literal["deferred", "excepted", "open"]
    reason: str = Field(min_length=3, max_length=500)
    until: str | None = None


class CheckPolicy(Model):
    check_id: str = Field(pattern=r"^H(?:0[1-9]|[12][0-9]|30)$")
    source_id: str
    target_id: str
    interval_seconds: int = Field(default=300, ge=60, le=604800)
    enabled: bool = False
    parameters: dict = Field(default_factory=dict)
    target_address: str | None = None

    @field_validator("enabled")
    @classmethod
    def draft_only(cls, value):
        if value:
            raise ValueError("Save the draft, then review and approve it before starting measurements")
        return value

    @field_validator("target_address")
    @classmethod
    def target_ip(cls, value):
        return str(ip_address(value)) if value is not None else None


class ProbeRequest(Model):
    source_id: str
    source_address: str

    @field_validator("source_address")
    @classmethod
    def address_valid(cls, value):
        address = ip_address(value)
        if address.is_multicast or address.is_unspecified:
            raise ValueError("Choose a unicast address assigned to the measurement source")
        return str(address)


class Login(Model):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Setup(Login):
    bootstrap_token: str = Field(min_length=32, max_length=128)


class Approval(Model):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=1)


class PasswordConfirmation(Model):
    password: str = Field(min_length=1, max_length=256)
    new_password: str | None = Field(default=None, max_length=256)
