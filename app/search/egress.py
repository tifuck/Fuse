"""Where web requests leave from: one of the user's connectors, or this server.

A user picks connectors under Settings › Web search. Every search, page read
and skill download of theirs then goes out through one of them, in turn over
the distinct public IPs they give. With none picked the server itself is used
only when WEB_SEARCH_SERVER_FALLBACK is on (off by default).

The server still resolves every name and hands the connector one vetted public
IP (the connector's `http` op is a pinned TLS pipe that does no DNS), so a
request through a connector can reach the user's own network no more than one
from the server can. What comes back is only as trustworthy as that machine:
results fetched through a connector are cached per user (`scope`).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import contextvars
import ipaddress
import time

import h11
import httpx

from ..config import settings
from .ssrf import is_blocked_host, public_ips

MIN_VERSION = (1, 11, 0)  # first connector with the `http` op
MACHINE_CONCURRENCY = 8  # of the connector's 128 job slots, shared with coding work
REQUEST_MAX = 65_536  # the connector refuses a larger request
HEAD_ROOM = 65_536  # status line and headers, on top of the body cap
HOP_SLACK = 1.5  # seconds a request may spend getting to the connector and back
NOT_SET_UP = (
    "Web access is not set up: no machine is selected for it (Settings › Web search). "
    "Do not retry; tell the user."
)
OFFLINE = (
    "Web access is unavailable: no machine selected for it is online with connector "
    f"{'.'.join(map(str, MIN_VERSION))} or newer (Settings › Web search). Do not retry; tell the user."
)
USER_HINT = "Web access is not available: pick an online machine under Settings › Web search."
_IP_ECHO = ("https://api.ipify.org/", "https://icanhazip.com/")
_SELECTED_TTL = 10.0

_via: contextvars.ContextVar[tuple[int, int] | None] = contextvars.ContextVar("fuse_web_egress", default=None)
_selected_cache: dict[int, tuple[float, list[tuple[int, str]]]] = {}
_turn: dict[int, int] = {}


class NoEgress(Exception):
    """The user has no way out to the web right now; str() is for the model."""


def supports(version: str) -> bool:
    try:
        return tuple(int(part) for part in str(version).split(".")[:3]) >= MIN_VERSION
    except ValueError:
        return False


def network(ip: str) -> str:
    """What two machines behind one router share: the IPv4 address, or the IPv6 /64."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    return str(addr) if addr.version == 4 else str(ipaddress.ip_network(f"{addr}/64", strict=False))


def _selected(user_id: int) -> list[tuple[int, str]]:
    """(host id, public IP) of the connectors the user picked."""
    user_id = int(user_id)
    hit = _selected_cache.get(user_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    from .. import db

    rows = db.fetchall(
        "SELECT id, web_ip FROM user_hosts WHERE user_id=? AND auth_type='agent' AND disabled=0 AND web_egress=1 "
        "ORDER BY id",
        (user_id,),
    )
    picked = [(int(row["id"]), row["web_ip"] or "") for row in rows]
    if len(_selected_cache) > 4096:
        _selected_cache.clear()
    _selected_cache[user_id] = (time.monotonic() + _SELECTED_TTL, picked)
    return picked


def available(user_id: int) -> bool:
    """Whether this user's chats get the web tools at all. Decided by what is
    set up, not by what is online, so the tool list does not change mid-chat."""
    return bool(_selected(user_id)) or bool(settings.web_search_server_fallback)


def scope(user_id: int) -> str:
    """Cache-key prefix: shared for server egress, the user's own otherwise."""
    return f"u{int(user_id)}|" if _selected(user_id) else ""


def _ready(user_id: int, host_id: int) -> bool:
    from .. import agent_hub

    return agent_hub.is_online(user_id, host_id) and supports(agent_hub.connector_version(user_id, host_id))


def pick(user_id: int) -> int | None:
    """The connector for the next request, None for this server."""
    picked = _selected(user_id)
    if not picked:
        if settings.web_search_server_fallback:
            return None
        raise NoEgress(NOT_SET_UP)
    # One per network: machines behind the same public IP only stand in for
    # each other when one is offline.
    by_network: dict[str, int] = {}
    for host_id, ip in picked:
        key = network(ip) or f"#{host_id}"
        if key not in by_network and _ready(user_id, host_id):
            by_network[key] = host_id
    if not by_network:
        raise NoEgress(OFFLINE)
    turn = _turn[user_id] = (_turn.get(user_id, -1) + 1) % 1_000_000
    return list(by_network.values())[turn % len(by_network)]


def enter(user_id: int) -> contextvars.Token:
    host_id = pick(user_id)
    return _via.set((int(user_id), host_id) if host_id is not None else None)


def leave(token: contextvars.Token) -> None:
    _via.reset(token)


@contextlib.contextmanager
def through(user_id: int):
    """Requests made inside leave the way this user's settings say."""
    token = enter(user_id)
    try:
        yield
    finally:
        leave(token)


def current() -> tuple[int, int] | None:
    """(user id, host id) of the connector this task sends through, None for the server."""
    return _via.get()


def key() -> str:
    """Names the exit a site sees, for state that follows an IP's reputation."""
    via = _via.get()
    return f"{via[0]}:{via[1]}" if via else ""


_ERRORS = {
    "timed out": httpx.ReadTimeout,
    "agent timed out": httpx.ReadTimeout,
    "connect failed": httpx.ConnectError,
    "tls failed": httpx.ConnectError,
    "invalid args": httpx.LocalProtocolError,
}


class ConnectorTransport(httpx.AsyncBaseTransport):
    """Sends each request through one connector's `http` op: the connector
    opens TLS to the IP given here, writes the request and relays the raw
    reply, which is parsed here. One request per connection, no redirects."""

    def __init__(self, user_id: int, host_id: int, max_bytes: int):
        self._user = int(user_id)
        self._host = int(host_id)
        self._max = int(max_bytes) + HEAD_ROOM
        self._slots = asyncio.Semaphore(MACHINE_CONCURRENCY)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme != "https":
            raise httpx.UnsupportedProtocol("only https leaves through a connector", request=request)
        port = url.port or 443
        name = str(request.extensions.get("sni_hostname") or url.host)
        ip = url.host
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            addrs = await asyncio.to_thread(public_ips, name, port)
            if not addrs:
                raise httpx.ConnectError("no public address", request=request) from None
            ip = addrs[0]
        if is_blocked_host(ip):
            raise httpx.ConnectError("not a public address", request=request)
        body = await request.aread()
        headers = [
            (k, v) for k, v in request.headers.raw
            if k.lower() not in (b"connection", b"transfer-encoding", b"content-length")
        ]
        headers.append((b"connection", b"close"))
        if body:
            headers.append((b"content-length", str(len(body)).encode()))
        conn = h11.Connection(h11.CLIENT)
        try:
            wire = conn.send(h11.Request(method=request.method, target=url.raw_path, headers=headers))
            if body:
                wire += conn.send(h11.Data(data=body))
            wire += conn.send(h11.EndOfMessage())
        except h11.LocalProtocolError as exc:
            raise httpx.LocalProtocolError(str(exc), request=request) from exc
        if len(wire) > REQUEST_MAX:
            raise httpx.LocalProtocolError("request too large for a connector", request=request)
        timeout = request.extensions.get("timeout") or {}
        budget = min(60.0, (timeout.get("connect") or 5.0) + (timeout.get("read") or 10.0) + HOP_SLACK)
        args = {
            "host": name, "ip": ip, "port": port, "req": base64.b64encode(wire).decode("ascii"),
            "max_bytes": self._max, "timeout_ms": int(budget * 1000),
        }
        async with self._slots:
            head, content = await self._exchange(conn, args, budget, request)
        return httpx.Response(
            head.status_code, headers=list(head.headers), stream=httpx.ByteStream(content),
            extensions={"http_version": b"HTTP/1.1"}, request=request,
        )

    async def _exchange(self, conn: h11.Connection, args: dict, budget: float, request: httpx.Request):
        from .. import agent_hub
        from ..hosts import HostError

        done = asyncio.Event()
        content = bytearray()
        head: list = []
        broken: list[str] = []

        def pump() -> None:
            while not done.is_set():
                try:
                    event = conn.next_event()
                except h11.RemoteProtocolError as exc:
                    broken.append(str(exc))
                    done.set()
                    return
                if event is h11.NEED_DATA or event is h11.PAUSED:
                    return
                if isinstance(event, h11.Response):
                    head.append(event)
                elif isinstance(event, h11.Data):
                    content.extend(event.data)
                    if len(content) > self._max:
                        done.set()  # the caller's own cap cuts it; stop the download
                elif isinstance(event, (h11.EndOfMessage, h11.ConnectionClosed)):
                    done.set()

        def on_chunk(stream: str, data: str) -> None:
            if stream != "b64" or done.is_set():
                return
            try:
                conn.receive_data(base64.b64decode(data, validate=True))
            except (binascii.Error, ValueError):
                broken.append("bad chunk from the connector")
                done.set()
                return
            pump()

        call = asyncio.ensure_future(
            agent_hub.call(self._user, self._host, "http", args, timeout=budget + 2.0, on_chunk=on_chunk)
        )
        waiter = asyncio.ensure_future(done.wait())
        try:
            await asyncio.wait({call, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if not done.is_set():
                try:
                    result = call.result()
                except HostError as exc:
                    raise _ERRORS.get(str(exc.detail), httpx.ConnectError)(str(exc.detail), request=request) from exc
                if not result.get("ok"):
                    error = str(result.get("error") or "read failed")
                    raise _ERRORS.get(error, httpx.ReadError)(error, request=request)
                if result.get("out") == "eof":
                    conn.receive_data(b"")
                    pump()
        finally:
            waiter.cancel()
            if not call.done():
                # The reply is complete: stop the connector waiting for the
                # site to close the connection.
                call.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await call
        if broken or not head:
            raise httpx.RemoteProtocolError(broken[0] if broken else "no response", request=request)
        return head[0], bytes(content)


def machines(user_id: int) -> list[dict]:
    """The user's connectors, for Settings › Web search."""
    from .. import agent_hub, db

    rows = db.fetchall(
        "SELECT id, alias, agent_version, web_egress, web_ip FROM user_hosts "
        "WHERE user_id=? AND auth_type='agent' AND disabled=0 ORDER BY alias",
        (int(user_id),),
    )
    out = []
    for row in rows:
        host_id = int(row["id"])
        online = agent_hub.is_online(user_id, host_id)
        version = agent_hub.connector_version(user_id, host_id) or row["agent_version"] or ""
        out.append({
            "id": host_id, "alias": row["alias"], "selected": bool(row["web_egress"]), "online": online,
            "version": version, "supported": supports(version), "ip": row["web_ip"] or "",
            "network": network(row["web_ip"] or ""),
        })
    return out


def overview(user_id: int) -> dict:
    """Settings › Web search: the connectors by the network they share."""
    groups: dict[str, list[dict]] = {}
    for machine in machines(user_id):
        groups.setdefault(machine["network"], []).append(machine)
    return {
        "available": available(user_id),
        "fallback": bool(settings.web_search_server_fallback),
        "min_version": ".".join(map(str, MIN_VERSION)),
        # Networks with a known IP first; the not-yet-read ones last.
        "groups": [{"network": net, "machines": groups[net]} for net in sorted(groups, key=lambda n: (not n, n))],
    }


def save(user_id: int, host_ids: list[int]) -> None:
    from .. import db

    wanted = {int(h) for h in host_ids}
    with db.tx() as conn:
        for row in conn.execute(
            "SELECT id, web_egress FROM user_hosts WHERE user_id=? AND auth_type='agent'", (int(user_id),)
        ).fetchall():
            on = 1 if int(row["id"]) in wanted else 0
            if on != int(row["web_egress"] or 0):
                conn.execute("UPDATE user_hosts SET web_egress=? WHERE id=?", (on, int(row["id"])))
    _selected_cache.pop(int(user_id), None)


async def detect_ip(user_id: int, host_id: int) -> str:
    """The public IP sites see from this connector, '' if it could not be read."""
    async with httpx.AsyncClient(
        transport=ConnectorTransport(user_id, host_id, 4096), timeout=httpx.Timeout(6.0, connect=3.0)
    ) as client:
        for url in _IP_ECHO:
            try:
                resp = await client.get(url, headers={"Accept": "text/plain", "Accept-Encoding": "identity"})
                addr = ipaddress.ip_address(resp.text.strip())
            except (httpx.HTTPError, ValueError):
                continue
            if resp.status_code == 200 and addr.is_global:
                return str(addr)
    return ""


async def detect(user_id: int) -> None:
    """Read and store the public IP of every connector that can be asked."""
    from .. import db

    ready = [m["id"] for m in machines(user_id) if _ready(user_id, m["id"])]
    found = await asyncio.gather(*(detect_ip(user_id, host_id) for host_id in ready))
    for host_id, ip in zip(ready, found):
        if ip:
            db.execute("UPDATE user_hosts SET web_ip=? WHERE id=? AND user_id=?", (ip, host_id, int(user_id)))
    _selected_cache.pop(int(user_id), None)
