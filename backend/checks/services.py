"""Pinned, source-bound service responses with an absolute time and byte budget."""

import re
import socket
import ssl
import time
from ipaddress import ip_address

from backend.checks.policies import LocalParameters
from backend.db import now


def response(source, target, parameters):
    policy = LocalParameters.model_validate(parameters)
    src, dst = ip_address(source), ip_address(target)
    if src.version != dst.version:
        raise ValueError("Source and destination address families differ")
    start = time.monotonic()
    deadline = start + 10
    evidence = {
        "source": str(src),
        "target": str(dst),
        "port": policy.port,
        "protocol": policy.protocol,
        "service_response_valid": False,
        "scope": "SSH protocol banner" if policy.protocol == "ssh" else "HTTP status response",
    }
    conn = socket.socket(socket.AF_INET6 if src.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            conn.bind((str(src), 0))
        except OSError:
            return {
                **evidence,
                "observed_at": now(),
                "supported": False,
                "reason": "source_binding_unavailable",
            }
        conn.settimeout(3)
        conn.connect((str(dst), policy.port))
        evidence["tcp_connected"] = True
        if policy.protocol == "https":
            conn.settimeout(min(3, max(0.001, deadline - time.monotonic())))
            conn = ssl.create_default_context().wrap_socket(conn, server_hostname=policy.hostname)
            evidence["tls_identity_verified"] = True
        if policy.protocol in {"https", "http"}:
            # The socket stays pinned to the reviewed IP; the hostname is only Host/SNI.
            request = (
                f"GET {policy.path} HTTP/1.1\r\nHost: {policy.hostname}\r\n"
                "Connection: close\r\nUser-Agent: Campus-Network-Ops/1\r\n\r\n"
            ).encode("ascii")
            conn.sendall(request)
        elif policy.protocol != "ssh":
            raise ValueError("Choose HTTP, HTTPS or SSH response verification")
        data = bytearray()
        while len(data) < 4096:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            conn.settimeout(min(remaining, 3))
            chunk = conn.recv(min(1024, 4096 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            lines = bytes(data).split(b"\n")[:-1]
            if policy.protocol in {"http", "https"} and lines:
                status = re.fullmatch(rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: [^\r\n]*)?\r?", lines[0])
                if status:
                    evidence["http_status"] = int(status[1])
                    evidence["expected_status"] = policy.expected_status
                    evidence["service_response_valid"] = int(status[1]) == policy.expected_status
                    evidence["reason"] = (
                        "expected_status" if evidence["service_response_valid"] else "unexpected_status"
                    )
                else:
                    evidence["reason"] = "invalid_http_response"
                break
            if policy.protocol == "ssh":
                banner = next((line for line in lines if line.startswith(b"SSH-")), None)
                if banner is not None:
                    valid = bool(re.fullmatch(rb"SSH-2\.0-[\x21-\x7e][\x20-\x7e]{0,248}\r?", banner))
                    evidence["service_response_valid"] = valid
                    evidence["reason"] = "ssh_banner_received" if valid else "unsupported_ssh_banner"
                    break
        if "reason" not in evidence:
            evidence["reason"] = "response_incomplete"
    except ssl.SSLError:
        evidence["reason"] = "tls_verification_failed"
    except OSError:
        evidence["reason"] = "service_connection_or_response_failed"
    finally:
        conn.close()
    return {**evidence, "observed_at": now(), "duration_ms": (time.monotonic() - start) * 1000}
