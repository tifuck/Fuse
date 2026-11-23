"""Web egress: which exit a user's requests take, and the connector transport.

No connector is real: agent_hub.call is a fake that plays a raw HTTP reply back
as the http op's chunk frames, the way a connector relays what a site sent.
"""
import asyncio
import base64
import gzip

import httpx
import pytest

from app import agent_hub, db, skill_install
from app.agent_hub import AgentError
from app.config import settings
from app.search import client, egress, engine
from app.search.engine import fetch_url, search_bulk
from app.search.providers import ddg
from app.search.providers.base import ProviderResponse

from ..helpers import quick_user
from ._support import hit, run

REAL_SELECTED = egress._selected
PAGE = (
    "<html><head><title>Doc</title></head><body><article><h1>Doc</h1>"
    "<p>{} has enough useful words to be read and extracted safely by the page reader.</p></article></body></html>"
)


def reply(body, status=b"200 OK", headers=b"Content-Type: text/html; charset=utf-8\r\n"):
    return b"HTTP/1.1 " + status + b"\r\n" + headers + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


class Connector:
    """agent_hub.call for the http op: relays ``reply`` in small chunks, then
    answers ``result`` (None: never, a site that keeps the connection open)."""

    def __init__(self, reply=b"", result={"ok": True, "out": "eof"}):
        self.reply = reply
        self.result = result
        self.jobs = []
        self.cancelled = 0

    async def __call__(self, user_id, host_id, op, args=None, timeout=30.0, on_chunk=None):
        self.jobs.append((user_id, host_id, op, dict(args)))
        for at in range(0, len(self.reply), 7):
            on_chunk("b64", base64.b64encode(self.reply[at:at + 7]).decode())
            await asyncio.sleep(0)
        if self.result is None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        if isinstance(self.result, BaseException):
            raise self.result
        return dict(self.result)

    def request(self, n=-1):
        return base64.b64decode(self.jobs[n][3]["req"])


def pick(monkeypatch, picked, online=None, version="1.11.0", only_user=None):
    """picked: [(host id, public ip)] for every user, or for only_user."""
    up = {host for host, _ in picked} if online is None else set(online)
    monkeypatch.setattr(egress, "_selected", lambda uid: list(picked) if only_user in (None, uid) else [])
    monkeypatch.setattr(agent_hub, "is_online", lambda uid, host: host in up)
    monkeypatch.setattr(agent_hub, "connector_version", lambda uid, host: version)


def connector(monkeypatch, *args, **kw):
    fake = Connector(*args, **kw)
    monkeypatch.setattr(agent_hub, "call", fake)
    return fake


# -- which exit ---------------------------------------------------------------

def test_no_machine_and_no_fallback_refuses_without_a_request(monkeypatch, web, dns):
    monkeypatch.setattr(settings, "web_search_server_fallback", False)
    dns.add("example.com", "93.184.216.10")
    web.page("https://example.com/doc", PAGE.format("The server"))

    assert not egress.available(1)
    [result] = run(search_bulk(["python asyncio"], user_id=1))
    extract, _, error = run(fetch_url("https://example.com/doc", user_id=1, allowed=set()))

    assert result.error == egress.NOT_SET_UP and not result.hits
    assert extract is None and error == egress.NOT_SET_UP
    assert web.requests == []


def test_requests_rotate_over_networks_not_machines(monkeypatch):
    picked = [(1, "203.0.113.5"), (2, "203.0.113.5"), (3, "198.51.100.7"), (4, "")]
    pick(monkeypatch, picked)
    assert [egress.pick(9) for _ in range(4)] == [1, 3, 4, 1]

    # The second machine behind 203.0.113.5 only stands in for the first.
    pick(monkeypatch, picked, online={2, 3})
    assert {egress.pick(9) for _ in range(4)} == {2, 3}


def test_offline_or_old_connectors_never_fall_back_to_the_server(monkeypatch, web, dns):
    dns.add("example.com", "93.184.216.10")
    web.page("https://example.com/doc", PAGE.format("The server"))
    for kw in ({"online": set()}, {"version": "1.10.0"}):
        pick(monkeypatch, [(5, "203.0.113.5")], **kw)
        extract, _, error = run(fetch_url("https://example.com/doc", user_id=1, allowed=set()))
        assert extract is None and error == egress.OFFLINE
    assert web.requests == []


@pytest.mark.usefixtures("fresh_db")
def test_a_user_picks_only_their_own_connectors(monkeypatch):
    monkeypatch.setattr(egress, "_selected", REAL_SELECTED)
    mine, other = quick_user("a@core.test"), quick_user("b@core.test")
    ids = {}
    for uid, alias, auth in ((mine, "pc", "agent"), (mine, "ssh", "password"), (other, "theirs", "agent")):
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO user_hosts (user_id, alias, hostname, username, auth_type, secret, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)", (uid, alias, "host.invalid", "dev", auth, "", db.now(), db.now()))
            ids[alias] = int(db.last_id(conn))

    egress.save(mine, list(ids.values()))

    assert REAL_SELECTED(mine) == [(ids["pc"], "")]  # not the SSH host, not someone else's
    assert REAL_SELECTED(other) == []
    assert [m["alias"] for m in egress.machines(mine)] == ["pc"]
    egress.save(mine, [])
    assert REAL_SELECTED(mine) == [] and not egress.scope(mine)


# -- through the connector ----------------------------------------------------

def test_a_page_read_leaves_through_the_connector_pinned_to_a_vetted_ip(monkeypatch, web, dns):
    dns.add("example.com", "93.184.216.10")
    pick(monkeypatch, [(5, "203.0.113.5")])
    body = gzip.compress(PAGE.format("The connector").encode())
    fake = connector(monkeypatch, reply(body, headers=b"Content-Type: text/html\r\nContent-Encoding: gzip\r\n"), result=None)

    extract, _, error = run(fetch_url("https://example.com/doc?x=1", user_id=1, allowed=set()))

    assert not error and "The connector" in extract.text
    [(user_id, host_id, op, args)] = fake.jobs
    assert (user_id, host_id, op) == (1, 5, "http")
    assert (args["host"], args["ip"], args["port"]) == ("example.com", "93.184.216.10", 443)
    sent = fake.request().lower()
    assert sent.startswith(b"get /doc?x=1 http/1.1\r\n") and b"\r\nhost: example.com\r\n" in sent
    assert b"\r\nconnection: close\r\n" in sent
    assert fake.cancelled == 1, "the reply was complete: the connector must not wait for the site to close"
    assert web.requests == [], "nothing may leave from the server"


def test_what_a_connector_answered_is_cached_for_its_user_only(monkeypatch, web, dns):
    dns.add("example.com", "93.184.216.10")
    web.page("https://example.com/doc", PAGE.format("The server"))
    pick(monkeypatch, [(5, "203.0.113.5")], only_user=1)
    fake = connector(monkeypatch, reply(PAGE.format("The connector").encode()))

    def read(user_id):
        return run(fetch_url("https://example.com/doc", user_id=user_id, allowed=set()))[0].text

    assert "The connector" in read(1)
    assert "The server" in read(2)
    assert "The connector" in read(1) and "The server" in read(2)
    assert len(fake.jobs) == 1 and len(web.requests) == 1


def test_the_transport_refuses_private_addresses_and_plain_http(monkeypatch, dns):
    dns.add("intranet.example", "10.0.0.8")
    fake = connector(monkeypatch, reply(b"ok"))

    async def get(url):
        async with httpx.AsyncClient(transport=egress.ConnectorTransport(1, 5, 1000)) as http:
            return await http.get(url)

    for url in ("https://10.0.0.5/", "https://127.0.0.1/", "https://intranet.example/", "https://[::1]/"):
        with pytest.raises(httpx.ConnectError):
            run(get(url))
    with pytest.raises(httpx.UnsupportedProtocol):
        run(get("http://example.com/"))
    assert fake.jobs == []


@pytest.mark.parametrize("result, raw, error", [
    ({"ok": False, "error": "timed out"}, b"", httpx.ReadTimeout),
    ({"ok": False, "error": "tls failed"}, b"", httpx.ConnectError),
    (AgentError("agent is offline"), b"", httpx.ConnectError),
    ({"ok": True, "out": "eof"}, b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort", httpx.RemoteProtocolError),
    ({"ok": True, "out": "eof"}, b"not http at all\r\n\r\n", httpx.RemoteProtocolError),
])
def test_connector_failures_surface_as_the_matching_http_error(monkeypatch, dns, result, raw, error):
    dns.add("example.com", "93.184.216.10")
    connector(monkeypatch, raw, result=result)

    async def get():
        async with httpx.AsyncClient(transport=egress.ConnectorTransport(1, 5, 1000)) as http:
            return await http.get("https://example.com/")

    with pytest.raises(error):
        run(get())


# -- what stays with the server, what follows the exit ----------------------------

def test_site_keys_never_travel_through_a_users_machine(monkeypatch):
    pick(monkeypatch, [(5, "203.0.113.5")])
    monkeypatch.setattr(settings, "brave_search_api_key", "site-brave-key")
    monkeypatch.setattr(settings, "github_token", "site-github-token")
    seen = {}

    def provider(name, hits):
        async def search(http, query, *, recency_days, timeout):
            seen[name] = http
            return ProviderResponse(hits=hits, status=200, error=None if hits else "empty")
        return search

    for attr in ("_ddg", "_bing", "_yahoo", "_hn", "_wiki", "_quotes"):
        monkeypatch.setattr(getattr(engine, attr), "search", provider(attr, [hit("https://example.com/a")]))
    monkeypatch.setattr(engine, "BraveProvider", lambda key: type("Brave", (), {"search": staticmethod(provider("brave", []))}))

    run(search_bulk(["python asyncio guide"], user_id=1))

    assert seen["brave"] is client.server_client()
    assert seen["_ddg"] is client._connector_clients[(1, 5)][0]
    assert skill_install._site_token(settings.github_token) == "site-github-token"
    with egress.through(1):
        assert skill_install._site_token(settings.github_token) == ""


def test_one_flagged_exit_does_not_pause_duckduckgo_for_the_others(monkeypatch):
    pick(monkeypatch, [(5, "203.0.113.5")])
    with egress.through(1):
        ddg._record_challenge()
        assert ddg.ddg_circuit_open()
    assert not ddg.ddg_circuit_open()
