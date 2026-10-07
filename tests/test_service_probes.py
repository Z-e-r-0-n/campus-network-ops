import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest

from backend.checks.evaluate import evaluate
from backend.checks.services import response


def connection(chunks):
    stream = MagicMock()
    stream.recv.side_effect = chunks
    return stream


@pytest.mark.parametrize(
    "protocol,port,reply,valid",
    [
        ("http", 80, b"HTTP/1.1 204 No Content\r\n", True),
        ("http", 80, b"HTTP/1.1 302 Found\r\nLocation: https://elsewhere.example\r\n", False),
        ("http", 80, b"SSH-2.0-Test\r\n", False),
        ("ssh", 22, b"Authorized use only\r\nSSH-2.0-Device_1\r\n", True),
        ("ssh", 22, b"SSH-1.5-obsolete\r\n", False),
    ],
)
def test_service_response_is_pinned_and_protocol_specific(protocol, port, reply, valid):
    stream = connection([reply, b""])
    params = {
        "protocol": protocol,
        "port": port,
        "hostname": "health.example.org",
        "path": "/health",
        "expected_status": 204,
    }
    with patch("backend.checks.services.socket.socket", return_value=stream):
        evidence = response("192.0.2.1", "192.0.2.2", params)
    stream.bind.assert_called_once_with(("192.0.2.1", 0))
    stream.connect.assert_called_once_with(("192.0.2.2", port))
    assert evidence["service_response_valid"] is valid
    assert evaluate("H04", evidence)["status"] == ("passed" if valid else "failed")
    if protocol == "http":
        assert b"Host: health.example.org\r\n" in stream.sendall.call_args.args[0]
        assert b"GET /health HTTP/1.1\r\n" in stream.sendall.call_args.args[0]
    else:
        stream.sendall.assert_not_called()
    stream.close.assert_called_once()
    assert "Device_1" not in str(evidence)


def test_https_verifies_expected_hostname_and_does_not_follow_redirects():
    stream, secured = connection([]), connection([b"HTTP/1.1 200 OK\r\n"])
    with (
        patch("backend.checks.services.socket.socket", return_value=stream),
        patch("backend.checks.services.ssl.create_default_context") as context,
    ):
        context.return_value.wrap_socket.return_value = secured
        evidence = response(
            "192.0.2.1", "192.0.2.2", {"protocol": "https", "port": 443, "hostname": "health.example.org"}
        )
        context.return_value.wrap_socket.assert_called_once_with(stream, server_hostname="health.example.org")
    assert evidence["tls_identity_verified"] is True
    assert evidence["service_response_valid"] is True
    secured.close.assert_called_once()


def test_invalid_certificate_is_failed_service_identity():
    stream = connection([])
    with (
        patch("backend.checks.services.socket.socket", return_value=stream),
        patch("backend.checks.services.ssl.create_default_context") as context,
    ):
        context.return_value.wrap_socket.side_effect = ssl.SSLCertVerificationError("private detail")
        evidence = response(
            "192.0.2.1", "192.0.2.2", {"protocol": "https", "port": 443, "hostname": "health.example.org"}
        )
    assert evidence["reason"] == "tls_verification_failed"
    assert "private detail" not in str(evidence)
    assert evaluate("H04", evidence)["status"] == "failed"


def test_local_bind_failure_is_unknown_and_sends_nothing():
    stream = connection([])
    stream.bind.side_effect = OSError("not assigned")
    with patch("backend.checks.services.socket.socket", return_value=stream):
        evidence = response("192.0.2.1", "192.0.2.2", {"protocol": "ssh", "port": 22})
    stream.connect.assert_not_called()
    assert evaluate("H04", evidence)["status"] == "unknown"


def test_total_timeout_and_response_size_are_bounded():
    stream = connection([b"x" * 1024] * 4)
    with patch("backend.checks.services.socket.socket", return_value=stream):
        evidence = response("192.0.2.1", "192.0.2.2", {"protocol": "ssh", "port": 22})
    assert evidence["reason"] == "response_incomplete"
    assert stream.recv.call_count == 4
    stream = connection([b"H", b"T", b"T", b"P"])
    with (
        patch("backend.checks.services.socket.socket", return_value=stream),
        patch("backend.checks.services.time.monotonic", side_effect=[0, 0, 4, 8, 12, 12]),
    ):
        evidence = response("192.0.2.1", "192.0.2.2", {"protocol": "ssh", "port": 22})
    assert evidence["reason"] == "service_connection_or_response_failed"
    assert stream.recv.call_count == 3


def test_service_address_families_must_match():
    with pytest.raises(ValueError, match="families"):
        response("::1", "127.0.0.1", {"protocol": "ssh", "port": 22})


def test_source_bound_tcp_against_real_local_listener():
    from backend.checks.collect import tcp

    # An ephemeral port is intentionally outside the approved service port set.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        with pytest.raises(ValueError, match="ports"):
            tcp("127.0.0.1", "127.0.0.1", port)
