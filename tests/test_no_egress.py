"""The app must not talk to the network.

This is the claim the README leads with -- "no LLM is called and nothing you paste
leaves the box" -- and it is the one thing a visitor cannot check by reading the code.
So it is asserted here instead: every socket is intercepted, a full redact/restore
round trip runs, and any connection to anything but loopback fails the test.

The guard sits at the socket layer deliberately. Patching `requests` or `httpx` would
only cover the client someone happens to use today; nothing reaches the network without
going through a socket, so anything added later -- telemetry, a crash reporter, a model
download at runtime -- trips this regardless of the library it was written with.
"""

import socket

import pytest
from fastapi.testclient import TestClient

from app.main import app

ENGINE = "spacy_sm"

PROMPT = (
    "Please email Jane Doe at jane.doe@example.com or call +1 (415) 555-0182. "
    "Their card is 4111 1111 1111 1111."
)

# Loopback is not egress: nothing leaves the machine. Everything else is.
LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}


class EgressAttempted(AssertionError):
    """Raised the moment anything tries to reach off-box."""


def _host(address):
    # AF_INET gives (host, port); AF_INET6 gives (host, port, flow, scope).
    if isinstance(address, tuple) and address:
        return address[0]
    return str(address)


@pytest.fixture
def attempts(monkeypatch):
    """Intercept sockets and DNS, recording anything aimed off this machine."""
    recorded = []

    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def guard(host, how):
        if host not in LOOPBACK:
            recorded.append(host + " (" + how + ")")
            raise EgressAttempted("Outbound connection attempted to " + host)

    def connect(self, address):
        guard(_host(address), "connect")
        return real_connect(self, address)

    def create_connection(address, *args, **kwargs):
        guard(_host(address), "create_connection")
        return real_create_connection(address, *args, **kwargs)

    # A DNS lookup is already egress -- the name being resolved has left the machine
    # even if the connection that follows never opens.
    def getaddrinfo(host, *args, **kwargs):
        guard(host if isinstance(host, str) else _host(host), "getaddrinfo")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    return recorded


def test_full_round_trip_makes_no_outbound_connection(attempts):
    """Startup, model load, detect, redact and restore -- all of it, offline."""
    # Inside the fixture's scope, so app startup is covered too, not just the requests.
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/config").status_code == 200

        analyzed = client.post(
            "/api/analyze", json={"text": PROMPT, "engine": ENGINE, "score_threshold": 0.4}
        )
        assert analyzed.status_code == 200, analyzed.text
        assert analyzed.json()["findings"], "nothing detected -- the model never loaded"

        redacted = client.post(
            "/api/redact", json={"text": PROMPT, "engine": ENGINE, "score_threshold": 0.4}
        )
        assert redacted.status_code == 200, redacted.text
        body = redacted.json()

        restored = client.post(
            "/api/restore",
            json={"text": body["redacted_text"], "session_id": body["session_id"]},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["restored_text"] == PROMPT

    assert attempts == [], "the app tried to reach the network: " + ", ".join(attempts)


def test_the_guard_actually_catches_egress(attempts):
    """Without this, a guard that silently stopped working would look like a pass.

    example.com's address is hardcoded rather than resolved -- resolving it would
    itself be egress, and this test must not depend on a network to prove a point
    about not using one.
    """
    with pytest.raises(EgressAttempted):
        socket.create_connection(("93.184.216.34", 80), timeout=1)

    with pytest.raises(EgressAttempted):
        socket.getaddrinfo("pypi.org", 443)

    assert len(attempts) == 2
