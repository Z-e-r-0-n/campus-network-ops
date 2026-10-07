"""Typed IOS-XE transport and configuration adapter. Qualification is external.

Nothing in this module enrolls or writes a device at import time. The executor
must provide an exact approved plan, qualified profile and target isolation.
"""

import json
import os
import re
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from uuid import UUID, uuid4

from scrapli.driver.core import IOSXEDriver

from backend.db import digest
from backend.execution.plans import ServiceSettings


def managed_services(config):
    result = {"ntp_servers": [], "syslog_servers": []}
    for line in config.splitlines():
        if not line.startswith(("ntp server ", "logging host ")):
            if re.match(r"^logging \d", line):
                raise ValueError("Legacy logging destination syntax is not qualified")
            continue
        match = re.fullmatch(r"(ntp server|logging host) (\S+)", line.strip())
        if not match:
            raise ValueError("Service destination options require a lossless qualified adapter")
        address = ip_address(match[2])
        if address.version != 4:
            raise ValueError("IPv6 service commands require separate firmware qualification")
        field = "ntp_servers" if match[1] == "ntp server" else "syslog_servers"
        result[field].append(str(address))
    return {key: sorted(set(values)) for key, values in result.items()}


def configuration_body(config, omit_service=None):
    """Remove known show-command metadata; preserve all unrelated configuration."""
    lines = []
    for line in config.splitlines():
        if not line.strip() or re.match(
            r"^(Building configuration\.\.\.|Current configuration\s*:|Using \d+ out of \d+ bytes|"
            r"! Last configuration change at |! NVRAM config last updated at )",
            line,
        ):
            continue
        prefix = {"ntp": "ntp server ", "syslog": "logging host "}.get(omit_service)
        if prefix and line.startswith(prefix):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def service_commands(service, before, after):
    settings = ServiceSettings(service=service, servers=after)
    before = sorted(set(str(ip_address(v)) for v in before))
    if any(ip_address(v).version != 4 for v in [*before, *settings.servers]):
        raise ValueError("IPv6 service commands require separate firmware qualification")
    prefix = "ntp server" if settings.service == "ntp" else "logging host"
    return [f"{prefix} {v}" for v in settings.servers if v not in before] + [
        f"no {prefix} {v}" for v in before if v not in settings.servers
    ]


class CiscoIOSXE:
    def __init__(self, profile, private_backup_directory, service_verifier):
        self.profile = profile
        self.backup_root = Path(private_backup_directory).resolve()
        self.verifier = service_verifier
        self.conn = None
        self.last_snapshot = None
        self.original_snapshot = None
        address = str(ip_address(profile["address"]))
        self.conn = IOSXEDriver(
            host=address,
            auth_username=profile["username"],
            auth_private_key=profile["identity_file"],
            auth_strict_key=True,
            ssh_known_hosts_file=profile["known_hosts"],
            ssh_config_file=profile["ssh_config"],
            transport="system",
            timeout_socket=8,
            timeout_transport=30,
            timeout_ops=30,
            channel_log=False,
        )

    def open(self):
        if not self.conn.isalive():
            self.conn.open()

    def read(self, command):
        self.open()
        response = self.conn.send_command(command)
        if response.failed:
            raise RuntimeError("The device rejected the requested read")
        return response.result

    def inspect(self):
        running, startup = self.read("show running-config"), self.read("show startup-config")
        if len(running) < 200 or len(startup) < 200:
            raise RuntimeError("Complete configuration evidence is unavailable")
        version = self.read("show version")
        serial = re.search(r"Processor board ID\s+(\S+)", version)
        firmware = re.search(r"Cisco IOS (?:XE )?Software, Version\s+([^\s,]+)", version)
        self.last_snapshot = {"running": running, "startup": startup}
        fields = managed_services(running)
        return {
            "serial": serial[1] if serial else None,
            "firmware": firmware[1] if firmware else None,
            "platform": "cisco_iosxe",
            "addresses": [self.profile["address"]],
            "managed_fields": fields,
            "configuration_digest": digest({k: configuration_body(v) for k, v in self.last_snapshot.items()}),
        }

    def identity_matches(self, expected, actual):
        return bool(
            expected.get("serial")
            and expected["serial"] == actual["serial"]
            and expected.get("platform") == actual["platform"]
            and expected.get("firmware") == actual["firmware"]
            and self.profile["address"] in expected.get("addresses", [])
        )

    def backup(self):
        if not self.last_snapshot:
            self.inspect()
        self.backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.backup_root.stat().st_mode & 0o077:
            raise ValueError("Private backup directory permissions need correction")
        directory = self.backup_root / str(uuid4())
        directory.mkdir(mode=0o700)
        hashes = {}
        for name, content in self.last_snapshot.items():
            path = directory / (name + ".txt")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            hashes[name] = sha256(content.encode()).hexdigest()
            if sha256(path.read_bytes()).hexdigest() != hashes[name]:
                raise RuntimeError("Backup readback failed")
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps(hashes))
        manifest.chmod(0o600)
        return {"verified": True, "reference": directory.name, "manifest_digest": digest(hashes)}

    def verify_backup(self, backup):
        try:
            reference = backup["reference"]
            if str(UUID(reference)) != reference:
                return False
            directory = self.backup_root / reference
            for path in [self.backup_root, directory]:
                if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
                    return False
                if path.stat().st_uid != os.getuid():
                    return False
            snapshots = {}
            for name in ["manifest.json", "running.txt", "startup.txt"]:
                path = directory / name
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_mode & 0o077
                    or path.stat().st_uid != os.getuid()
                    or path.stat().st_size > 16 * 1024 * 1024
                ):
                    return False
                snapshots[name] = path.read_text()
            hashes = json.loads(snapshots.pop("manifest.json"))
            if digest(hashes) != backup["manifest_digest"]:
                return False
            original = {k.removesuffix(".txt"): v for k, v in snapshots.items()}
            if any(sha256(v.encode()).hexdigest() != hashes[k] for k, v in original.items()):
                return False
            self.original_snapshot = original
            return True
        except (KeyError, TypeError, ValueError, OSError):
            return False

    def unchanged_context(self, content):
        if not self.original_snapshot or not self.last_snapshot:
            raise RuntimeError("Verified original configuration is required")
        original = {k: configuration_body(v) for k, v in self.original_snapshot.items()}
        # Saving the running config must never persist an unrelated, pre-existing difference.
        if original["running"] != original["startup"]:
            raise RuntimeError("Running and saved configuration differed before this plan")
        service = content["actions"][0]["parameters"]["service"]
        baseline = configuration_body(self.original_snapshot["running"], service)
        if any(configuration_body(v, service) != baseline for v in self.last_snapshot.values()):
            raise RuntimeError("Unrelated configuration changed; automatic persistence is blocked")

    def recovery_ready(self, content):
        return self.profile.get("recovery_access_verified") is True and content["runbook_id"] == "RB02"

    def apply(self, action, content):
        if action["type"] != "service.replace_servers":
            raise ValueError("This adapter action requires additional firmware qualification")
        settings = ServiceSettings(**action["parameters"])
        field = settings.service + "_servers"
        live = self.inspect()["managed_fields"]
        self.unchanged_context(content)
        if live != content["recovery"]["before"]:
            raise RuntimeError("Managed settings changed before execution")
        commands = service_commands(settings.service, live[field], settings.servers)
        if commands:
            reply = self.conn.send_configs(commands, stop_on_failed=True)
            if reply.failed:
                raise ConnectionError("Device change needs state reconciliation")

    def reconcile(self, content, live):
        if content["runbook_id"] != "RB02":
            return "unknown"
        current = live["managed_fields"]
        before = content["recovery"]["before"]
        after = {**before, content["change"]["field"]: content["change"]["after"]}
        if current == after:
            return "applied"
        if current == before:
            return "not_applied"
        # Only subsets of this exact managed-field transition qualify for recovery.
        changed = content["change"]["field"]
        if all(current.get(k) == v for k, v in before.items() if k != changed):
            allowed = set(before[changed]) | set(after[changed])
            if set(current.get(changed, [])) <= allowed:
                return "partial"
        return "unknown"

    def recover(self, recovery, content):
        live = self.inspect()
        self.unchanged_context(content)
        if self.reconcile(content, live) not in {"applied", "partial"}:
            raise RuntimeError("Intervening configuration prevents automatic recovery")
        service = content["actions"][0]["parameters"]["service"]
        field = service + "_servers"
        commands = service_commands(service, live["managed_fields"][field], recovery["before"][field])
        if commands and self.conn.send_configs(commands, stop_on_failed=True).failed:
            raise RuntimeError("Recovery command failed")

    def verify_services(self, policy, observation_seconds, recovering=False):
        return self.verifier(policy, observation_seconds, recovering) is True

    def persist(self, content, recovering=False):
        live = self.inspect()
        self.unchanged_context(content)
        expected = "not_applied" if recovering else "applied"
        if self.reconcile(content, live) != expected:
            raise RuntimeError("Configuration no longer matches the approved persistence state")
        if self.conn.send_command("write memory").failed:
            raise RuntimeError("Configuration save failed")

    def verify_persistence(self, content):
        self.inspect()
        self.unchanged_context(content)
        return configuration_body(self.last_snapshot["startup"]) == configuration_body(
            self.last_snapshot["running"]
        )

    def verify_recovery(self, content):
        return self.inspect()["managed_fields"] == content["recovery"]["before"] and self.verify_persistence(
            content
        )

    def close(self):
        if self.conn:
            self.conn.close()
