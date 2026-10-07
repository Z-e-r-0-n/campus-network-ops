"""Pinned endpoints and bounded, no-redirect HTTP; no ambient proxy credentials."""

import json
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx


class ConnectorUnavailable(Exception):
    pass


class PinnedHTTP:
    def __init__(self, config, headers=None):
        self.config = config
        self.headers = headers or {}

    def request(self, method, route, body=None, headers=None, with_metadata=False):
        base = urlsplit(self.config["endpoint"])
        if not route.startswith("/") or ".." in route or "://" in route:
            raise ValueError("Invalid connector route")
        address = str(ip_address(self.config["pinned_address"]))
        if ":" in address:
            address = f"[{address}]"
        port = base.port or (443 if base.scheme == "https" else 80)
        url = f"{base.scheme}://{address}:{port}{base.path.rstrip('/')}{route}"
        request_headers = {**self.headers, **(headers or {}), "Host": base.netloc}
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=15) as client:
                with client.stream(
                    method,
                    url,
                    json=body,
                    headers=request_headers,
                    extensions={"sni_hostname": base.hostname},
                ) as response:
                    if response.status_code >= 300:
                        raise ConnectorUnavailable(f"Connector returned HTTP {response.status_code}")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 8_000_000:
                            raise ConnectorUnavailable("Connector response exceeds its size limit")
                        chunks.append(chunk)
                    data = json.loads(b"".join(chunks))
                    if with_metadata:
                        return {"data": data, "etag": response.headers.get("etag")}
                    return data
        except (httpx.HTTPError, ValueError):
            raise ConnectorUnavailable("Connector could not return a valid bounded response") from None
