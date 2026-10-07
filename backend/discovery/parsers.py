"""Vendor output parsers; no site names, addresses or expected campus topology."""

import re
from ipaddress import ip_address


def valid_addresses(values):
    result = []
    for value in values:
        try:
            result.append(str(ip_address(value)))
        except ValueError:
            pass
    return sorted(set(result))


def cisco_identity(output):
    serial = re.search(r"Processor board ID\s+(\S+)", output)
    version = re.search(
        r"(?:Cisco IOS (?:XE )?Software.*?Version|Cisco IOS XE Software, Version)\s+([^\s,]+)", output
    )
    model = re.search(r"(?im)^\s*Model [Nn]umber\s*:\s*([A-Za-z0-9_-]+)", output)
    if not model:
        model = re.search(r"(?im)^cisco\s+((?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+)\s+\(", output)
    hostname = re.search(r"(?m)^(\S+) uptime is", output)
    return {
        "platform": "cisco_iosxe",
        "serial": serial[1] if serial else None,
        "firmware": version[1] if version else None,
        "model": model[1] if model else None,
        "label": hostname[1] if hostname else None,
    }


def cdp_neighbors(output):
    results = []
    for block in re.split(r"(?m)^Device ID:\s*", output)[1:]:
        name = block.splitlines()[0].strip()
        addresses = valid_addresses(re.findall(r"(?:IP(?:v4|v6)? address|IPv6 Address):\s*(\S+)", block))
        local = re.search(r"Interface:\s*([^,\n]+)", block)
        remote = re.search(r"Port ID \(outgoing port\):\s*([^\r\n]+)", block)
        results.append(
            {
                "address": addresses[0] if addresses else None,
                "advertised_addresses": addresses,
                "name": name,
                "local_port": local[1].strip() if local else "",
                "remote_port": remote[1].strip() if remote else "",
                "protocol": "cdp",
            }
        )
    return results


def lldp_neighbors(output):
    results = []
    for block in re.split(r"(?im)^\s*Local (?:Intf|Interface|Port)\s*:\s*", output)[1:]:
        local = block.splitlines()[0].strip()
        remote = re.search(r"(?im)^\s*Port (?:id|ID)\s*:\s*(.+)$", block)
        name = re.search(r"(?im)^\s*System Name\s*:\s*(.+)$", block)
        addresses = valid_addresses(
            re.findall(r"(?im)(?:IP(?:v4|v6)?(?: address)?|Management Address)\s*:\s*(\S+)", block)
        )
        results.append(
            {
                "address": addresses[0] if addresses else None,
                "advertised_addresses": addresses,
                "name": name[1].strip() if name else None,
                "local_port": local,
                "remote_port": remote[1].strip() if remote else "",
                "protocol": "lldp",
            }
        )
    return results


def dlink_identity(output):
    def field(pattern):
        match = re.search(pattern + r"\s*[:=]\s*([^\r\n]+)", output, re.I)
        return match[1].strip() if match else None

    return {
        "platform": "dlink",
        "label": field(r"(?:System Name|Device Name)"),
        "model": field(r"(?:Device Type|Model Name)"),
        "serial": field(r"Serial Number"),
        "firmware": field(r"(?:Firmware Version|Firmware Build)"),
        "chassis_mac": field(r"(?:MAC Address|System MAC)"),
    }


def capabilities(platform, outputs):
    # Evidence describes functions. Core/distribution/access are intended roles requiring review.
    return {
        "identity_read": bool(outputs.get("identity")),
        "neighbor_read": bool(outputs.get("neighbors")),
        "platform": platform,
        "dedicated_account_verified": False,
        "write_qualified": False,
    }
