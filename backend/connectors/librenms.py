from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from backend.connectors.http import PinnedHTTP

DEVICE_FIELDS = [
    "device_id",
    "hostname",
    "sysName",
    "os",
    "hardware",
    "version",
    "status",
    "last_polled",
    "last_discovered",
    "location",
    "serial",
]
LINK_FIELDS = [
    "id",
    "local_port_id",
    "local_device_id",
    "remote_port_id",
    "remote_device_id",
    "remote_hostname",
    "remote_port",
    "protocol",
    "active",
]
PORT_FIELDS = [
    "port_id",
    "device_id",
    "ifIndex",
    "ifName",
    "ifDescr",
    "ifSpeed",
    "ifOperStatus",
    "ifAdminStatus",
    "ifMtu",
    "ifInErrors",
    "ifOutErrors",
    "ifInUcastPkts",
    "ifOutUcastPkts",
]


def select(row, fields):
    return {key: row[key] for key in fields if key in row}


def poll_time(value, zone):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if not zone:
                return None
            parsed = parsed.replace(tzinfo=ZoneInfo(zone))
        if parsed > datetime.now(UTC):
            return None
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, TypeError, KeyError):
        return None


class LibreNMS:
    def __init__(self, connector, token):
        self.connector = connector
        self.http = PinnedHTTP(connector, {"X-Auth-Token": token})

    def snapshot(self):
        devices = self.http.request("GET", "/devices").get("devices", [])
        links = self.http.request("GET", "/resources/links").get("links", [])
        return {
            "devices": [select(d, DEVICE_FIELDS) for d in devices],
            "links": [select(link, LINK_FIELDS) for link in links],
            "source": "librenms",
            "collected_at": datetime.now(UTC).isoformat(),
        }

    def ports(self, device_id):
        if not str(device_id).isdigit():
            raise ValueError("Invalid monitoring device ID")
        data = self.http.request("GET", f"/devices/{device_id}/ports?columns=" + ",".join(PORT_FIELDS))
        return [select(port, PORT_FIELDS) for port in data.get("ports", [])]


class SnapshotReader:
    """Traverse existing monitoring evidence without presenting it as a fresh CLI read."""

    def __init__(self, snapshot, source_timezone="UTC"):
        self.devices = {str(d["device_id"]): d for d in snapshot["devices"]}
        self.by_address = {d.get("hostname"): d for d in snapshot["devices"]}
        self.links = snapshot["links"]
        self.zone = source_timezone

    def __call__(self, address):
        record = self.by_address.get(address)
        if not record:
            return {
                "addresses": [address],
                "label": address,
                "source": "librenms",
                "neighbors": [],
                "gap": "No monitoring identity; dedicated access required",
            }
        neighbors = []
        for link in self.links:
            if str(link.get("local_device_id")) != str(record["device_id"]):
                continue
            remote = self.devices.get(str(link.get("remote_device_id")), {})
            target = remote.get("hostname") or link.get("remote_hostname")
            neighbors.append(
                {
                    "address": target,
                    "local_port": str(link.get("local_port_id", "")),
                    "remote_port": str(link.get("remote_port", "")),
                    "protocol": link.get("protocol", "unknown"),
                }
            )
        platform = str(record.get("os", "unknown"))
        role = "firewall" if platform == "pfsense" else "unknown"
        return {
            "addresses": [address],
            "label": record.get("sysName") or address,
            "platform": platform,
            "model": record.get("hardware"),
            "firmware": record.get("version"),
            "serial": record.get("serial"),
            "role": role,
            "source": "librenms",
            "observed_at": poll_time(record.get("last_polled"), self.zone),
            "monitoring_id": str(record["device_id"]),
            "neighbors": neighbors,
        }
