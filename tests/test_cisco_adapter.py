from types import SimpleNamespace

import pytest

from backend.adapters.cisco import CiscoIOSXE, configuration_body, managed_services, service_commands

BASE = "\n".join(
    [
        "version 17.1",
        "hostname example-switch",
        "!",
        *[f"interface GigabitEthernet1/0/{n}\n description example-port-{n}\n!" for n in range(1, 5)],
        "ntp server 192.0.2.10",
        "logging host 192.0.2.20",
        "end",
    ]
)
AFTER = BASE.replace("ntp server 192.0.2.10", "ntp server 192.0.2.11")
CONTENT = {
    "runbook_id": "RB02",
    "actions": [
        {"type": "service.replace_servers", "parameters": {"service": "ntp", "servers": ["192.0.2.11"]}}
    ],
    "change": {"field": "ntp_servers", "after": ["192.0.2.11"]},
    "recovery": {"before": managed_services(BASE)},
}


@pytest.fixture
def adapter(tmp_path):
    item = CiscoIOSXE.__new__(CiscoIOSXE)
    item.profile = {"address": "192.0.2.1"}
    item.backup_root = tmp_path / "backups"
    item.last_snapshot = {"running": BASE, "startup": BASE}
    item.original_snapshot = None
    item.configurations = {"running": AFTER, "startup": BASE}
    item.saved_commands = []
    item.conn = SimpleNamespace(
        send_command=lambda command: item.saved_commands.append(command) or SimpleNamespace(failed=False)
    )

    def read(command):
        if command == "show version":
            return "Cisco IOS XE Software, Version 17.1\nProcessor board ID EXAMPLE"
        return item.configurations["running" if command == "show running-config" else "startup"]

    item.read = read
    return item


@pytest.mark.parametrize(
    "line",
    [
        "ntp server 192.0.2.10 prefer",
        "ntp server 192.0.2.10 key 1",
        "ntp server vrf management 192.0.2.10",
        "logging host 192.0.2.20 transport tcp port 6514",
        "logging 192.0.2.20",
        "ntp server 2001:db8::1",
    ],
)
def test_unsupported_options_are_not_silently_lost(line):
    with pytest.raises(ValueError):
        managed_services(line)


def test_ipv6_commands_require_separate_qualification():
    with pytest.raises(ValueError):
        service_commands("ntp", ["192.0.2.10"], ["2001:db8::1"])


def test_backup_reference_is_private_and_content_verified(adapter):
    backup = adapter.backup()
    assert adapter.verify_backup(backup)
    assert adapter.original_snapshot == {"running": BASE, "startup": BASE}
    assert "hostname" not in str(backup)
    path = adapter.backup_root / backup["reference"] / "running.txt"
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text(AFTER)
    assert not adapter.verify_backup(backup)
    assert not adapter.verify_backup({**backup, "reference": "../outside"})


def test_symlink_backup_rejected(adapter, tmp_path):
    backup = adapter.backup()
    path = adapter.backup_root / backup["reference"] / "running.txt"
    other = tmp_path / "other"
    other.write_text(BASE)
    path.unlink()
    path.symlink_to(other)
    assert not adapter.verify_backup(backup)


def test_unrelated_change_alters_snapshot_digest(adapter):
    first = adapter.inspect()["configuration_digest"]
    adapter.configurations["running"] = AFTER.replace("example-port-1", "operator-change")
    assert adapter.inspect()["configuration_digest"] != first


def test_known_show_headers_do_not_look_like_config_drift():
    assert configuration_body(
        "Building configuration...\nCurrent configuration : 999 bytes\n" + BASE
    ) == configuration_body("Using 999 out of 12345 bytes\n" + BASE)


@pytest.mark.parametrize("which", ["running", "startup", "original_startup"])
def test_persistence_cannot_save_unrelated_changes(adapter, which):
    backup = adapter.backup()
    assert adapter.verify_backup(backup)
    if which == "original_startup":
        adapter.original_snapshot["startup"] = BASE.replace("example-port-1", "earlier-unsaved-change")
    else:
        adapter.configurations[which] = adapter.configurations[which].replace(
            "example-port-1", "unapproved-change"
        )
    with pytest.raises(RuntimeError):
        adapter.persist(CONTENT)
    assert adapter.saved_commands == []


def test_approved_service_change_can_be_saved_and_verified(adapter):
    assert adapter.verify_backup(adapter.backup())
    adapter.persist(CONTENT)
    assert adapter.saved_commands == ["write memory"]
    assert not adapter.verify_persistence(CONTENT)
    adapter.configurations["startup"] = AFTER
    assert adapter.verify_persistence(CONTENT)
    adapter.configurations["running"] = BASE
    adapter.persist(CONTENT, recovering=True)
    adapter.configurations["startup"] = BASE
    assert adapter.verify_recovery(CONTENT)
