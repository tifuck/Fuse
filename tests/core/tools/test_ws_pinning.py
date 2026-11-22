"""A monitor's WebSocket connects only to a globally routable address.

workspace pins the socket to the address it resolved; an answer that reaches
a private address through an IPv6 wrapper (NAT64, IPv4-compatible, mapped)
is refused before any connection is attempted. The resolver is faked.
"""
import asyncio
import socket

import pytest

from app import workspace


@pytest.mark.parametrize("addr", ["64:ff9b::7f00:1", "::7f00:1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "fd00::1"])
def test_a_wrapped_private_address_is_never_connected_to(monkeypatch, addr):
    probe = asyncio.new_event_loop()
    loop_cls = type(probe)  # the class asyncio.run creates
    probe.close()
    connected = []

    async def getaddrinfo(self, host, port, **kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, port, 0, 0))]

    async def sock_connect(self, sock, address):
        connected.append(address)

    monkeypatch.setattr(loop_cls, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(loop_cls, "sock_connect", sock_connect)

    with pytest.raises(OSError, match="non-public"):
        asyncio.run(workspace._pinned_public_sock("wss://feed.example/ws"))
    assert connected == []
