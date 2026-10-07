"""Explicitly mapped key access with reviewed host identities and durable holds."""

import base64
import os
import subprocess
from hashlib import sha256
from ipaddress import ip_address, ip_network

from backend.config import private_read
from backend.db import Conflict, digest, now
from backend.discovery.parsers import cdp_neighbors, cisco_identity, dlink_identity, lldp_neighbors


def eligible(profile, address):
    addr = ip_address(address)
    return any(addr in ip_network(prefix) for prefix in profile["prefixes"])


def scan_host(store, trust_id):
    with store.tx() as tx:
        record = tx.get("host_trust", trust_id)
        profile = tx.get("access_profile", record["profile_id"])
    if not eligible(profile, record["address"]):
        raise ValueError("Host is outside the selected access profile")
    if record["status"] in {"approved", "superseded", "awaiting_review"}:
        return {"id": trust_id, "status": record["status"]}
    try:
        reply = subprocess.run(
            [
                "ssh-keyscan",
                "-T",
                "3",
                "-p",
                str(profile["port"]),
                "-t",
                "ed25519,ecdsa,rsa",
                str(ip_address(record["address"])),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        output = reply.stdout[:32768]
    except (subprocess.TimeoutExpired, OSError):
        output = ""
    keys, fingerprints = [], []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[1] not in {
            "ssh-ed25519",
            "ssh-rsa",
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
        }:
            continue
        try:
            from cryptography.hazmat.primitives.serialization import load_ssh_public_key

            load_ssh_public_key((fields[1] + " " + fields[2]).encode())
            fingerprint = "SHA256:" + base64.b64encode(
                sha256(base64.b64decode(fields[2], validate=True)).digest()
            ).decode().rstrip("=")
        except (ValueError, TypeError):
            continue
        host = record["address"] if profile["port"] == 22 else f"[{record['address']}]:{profile['port']}"
        keys.append(f"{host} {fields[1]} {fields[2]}")
        fingerprints.append({"algorithm": fields[1], "fingerprint": fingerprint})
    with store.tx(write=True) as tx:
        current = tx.get("host_trust", trust_id)
        if current["status"] in {"approved", "superseded", "awaiting_review"}:
            return {"id": trust_id, "status": current["status"]}
        if tx.get("access_profile", record["profile_id"])["revision"] != record["profile_revision"]:
            raise Conflict("Access profile changed during host identity collection")
        record = tx.put(
            "host_trust",
            {
                **current,
                "status": "awaiting_review" if keys else "unavailable",
                "keys": keys,
                "fingerprints": fingerprints,
                "fingerprint_digest": digest(sorted(keys)),
                "observed_at": now(),
                "identity_verified": False,
            },
            trust_id,
        )
        tx.audit("access", "host_key.observed", trust_id, {"status": record["status"]})
    return {"id": trust_id, "status": record["status"]}


class DirectReader:
    def __init__(self, store, settings, profile_ids):
        self.store, self.settings = store, settings
        with store.tx() as tx:
            self.profiles = [tx.get("access_profile", key) for key in profile_ids]

    def __call__(self, address):
        matches = [profile for profile in self.profiles if eligible(profile, address)]
        observation = {
            "addresses": [address],
            "label": address,
            "source": "ssh",
            "neighbors": [],
            "observed_at": None,
        }
        if len(matches) != 1:
            return {
                **observation,
                "gap": "Map one reviewed access profile to this address"
                if not matches
                else "Multiple access profiles match; resolve the overlap",
            }
        profile = matches[0]
        key = digest(
            {"address": address, "profile_id": profile["id"], "profile_revision": profile["revision"]}
        )
        with self.store.tx() as tx:
            hold = tx.get("access_hold", key, False)
            trust = tx.c.execute(
                "SELECT * FROM records WHERE kind='host_trust' AND data->>'address'=%s AND data->>'profile_id'=%s AND data->>'status'='approved' ORDER BY updated DESC LIMIT 1",
                (address, profile["id"]),
            ).fetchone()
            trusted = tx.unpack(trust) if trust else None
        if hold and not hold.get("released_at"):
            return {**observation, "gap": "Access is held after a failed attempt; review it before retrying"}
        if not trusted:
            return {**observation, "gap": "Review the device SSH host fingerprint before authentication"}
        return self.read(profile, trusted, address, key, observation)

    def read(self, profile, trust, address, hold_key, observation):
        from scrapli.driver import GenericDriver
        from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliConnectionNotOpened, ScrapliTimeout

        private_key = self.settings.state / "secrets" / profile["key_ref"]
        private_read(private_key)
        directory = self.settings.state / "ssh" / profile["id"]
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        known = directory / (digest(address) + ".known_hosts")
        fd = os.open(known, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(trust["keys"]) + "\n")
        config = directory / "ssh_config"
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(
                "Host *\n  BatchMode yes\n  PasswordAuthentication no\n  KbdInteractiveAuthentication no\n  PreferredAuthentications publickey\n  IdentitiesOnly yes\n  StrictHostKeyChecking yes\n  ConnectTimeout 8\n"
            )
        try:
            with GenericDriver(
                host=address,
                port=profile["port"],
                auth_username=profile["username"],
                auth_private_key=str(private_key),
                auth_strict_key=True,
                ssh_known_hosts_file=str(known),
                ssh_config_file=str(config),
                timeout_socket=8,
                timeout_transport=30,
                timeout_ops=30,
                channel_log=False,
                transport="system",
            ) as connection:
                platform = profile["platform"]
                command = (
                    "show switch"
                    if platform == "dlink"
                    else "uname -s"
                    if platform in {"linux", "freebsd"}
                    else "show version"
                )
                response = connection.send_command(command)
                text = response.result[:131072]
                if platform == "auto":
                    platform = (
                        "cisco_iosxe" if "Cisco IOS" in text else "dlink" if "D-Link" in text else "unknown"
                    )
                if platform == "cisco_iosxe":
                    identity = cisco_identity(text)
                    cdp = connection.send_command("show cdp neighbors detail").result[:131072]
                    lldp = connection.send_command("show lldp neighbors detail").result[:131072]
                    neighbors = cdp_neighbors(cdp) + lldp_neighbors(lldp)
                elif platform == "dlink":
                    identity, neighbors = dlink_identity(text), []
                elif platform in {"linux", "freebsd"} and text.strip() in {"Linux", "FreeBSD"}:
                    identity, neighbors = {"platform": platform}, []
                else:
                    return {
                        **observation,
                        "gap": "Authenticated platform requires a supported identity parser",
                    }
                result = {
                    **observation,
                    **identity,
                    "label": identity.get("label") or address,
                    "neighbors": neighbors,
                    "observed_at": now(),
                    "role": "unknown",
                    "capabilities": {"identity_read": True, "neighbor_read": platform == "cisco_iosxe"},
                }
                if platform != "cisco_iosxe":
                    result["gap"] = (
                        "Identity collected; neighbor protocol collection is not yet qualified for this platform"
                    )
                return result
        except (ScrapliAuthenticationFailed, ScrapliConnectionNotOpened, ScrapliTimeout, OSError):
            with self.store.tx(write=True) as tx:
                tx.put(
                    "access_hold",
                    {
                        "address": address,
                        "profile_id": profile["id"],
                        "at": now(),
                        "reason": "SSH authentication, host trust or connection failed",
                    },
                    hold_key,
                )
            return {**observation, "gap": "SSH access failed; further attempts are held for review"}
