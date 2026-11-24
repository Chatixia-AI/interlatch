"""Reaching the hub at all is given a short wait of its own, and is not tried twice.

A hub behind a firewall (a company VPN the computer is not on) or a switched-off one answers nothing. The client had
one 120 s timeout for everything, so the system's own connect timeout decided (75 s on macOS), and the retry meant for
a kept-alive connection the hub had closed asked a second time: every background sync waited 150 s on such a hub.
"""

import socket
import threading

import pytest

from interlatch.sharing.client import CONNECT_TIMEOUT_S, HubClient
from interlatch.sharing.protocol import HubUnreachable


def test_a_hub_that_does_not_answer_is_given_up_on_quickly_and_once(monkeypatch):
    asked = []

    def no_answer(address, timeout=None, *args, **kwargs):
        asked.append((address, timeout))
        raise TimeoutError("timed out")

    monkeypatch.setattr(socket, "create_connection", no_answer)
    client = HubClient("http://hub.invalid:8080", "token")
    with pytest.raises(HubUnreachable, match=r"can't reach the hub at http://hub\.invalid:8080: timed out"):
        client.request("GET", "/api/hub/hello")
    assert asked == [(("hub.invalid", 8080), CONNECT_TIMEOUT_S)]
    assert CONNECT_TIMEOUT_S <= 15 and client.conn is None


def test_a_shorter_timeout_asked_for_is_kept_for_connecting(monkeypatch):
    asked = []

    def no_answer(address, timeout=None, *args, **kwargs):
        asked.append(timeout)
        raise TimeoutError("timed out")

    monkeypatch.setattr(socket, "create_connection", no_answer)
    with pytest.raises(HubUnreachable):
        HubClient("http://hub.invalid", "", timeout=2).request("GET", "/api/hub/hello")
    assert asked == [2]


@pytest.fixture()
def closing_hub():
    """A hub that answers each request as if it kept the connection, then closes it. Yields (url, connections seen)."""
    server = socket.create_server(("127.0.0.1", 0))
    seen = []

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:  # closed: the test is over
                return
            with conn:
                seen.append(conn.getpeername())
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 12\r\n\r\n"
                             b'{"ok": true}')

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}", seen
    server.close()
    thread.join(timeout=5)


def test_after_connecting_the_hub_gets_the_long_wait(closing_hub):
    url, _ = closing_hub
    client = HubClient(url, "token")
    assert client.request("GET", "/api/hub/hello") == {"ok": True}
    assert client.conn.sock.gettimeout() == client.timeout == 120  # a big push, a slow hub: not the connect's 10 s
    client.close()


def test_a_kept_alive_connection_the_hub_closed_still_gets_one_fresh_retry(closing_hub):
    url, seen = closing_hub
    client = HubClient(url, "token")
    assert client.request("GET", "/api/hub/hello") == {"ok": True}
    assert client.request("GET", "/api/hub/hello") == {"ok": True}  # the first connection is gone: asked again on a new one
    assert len(seen) == 2
    client.close()
