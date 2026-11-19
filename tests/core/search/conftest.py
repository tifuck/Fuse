"""Search area fixtures: process-wide search state starts empty in every test,
DNS is a fake resolver and the network is a scripted httpx transport.

The engine keeps a query cache, a page cache, a blocked-host list, the DDG and
Brave circuit state, the domain-policy cache and the per-user rate limiter in
module globals; a test must never see another test's. Both shared httpx
clients are replaced by clients over ``web.handler`` behind the real
CappedTransport, so every request (search engines, page reads, adapters) is
answered by the test or fails as a connect error: nothing reaches the network.
"""
import math
import socket
from types import SimpleNamespace

import httpx
import pytest

from app import security
from app.config import settings
from app.search import client, engine, excerpts, policy, ssrf
from app.search.extract import PDF_MAX_BYTES
from app.search.providers import brave, ddg


class Web:
    """Scripted hosts. Routes match the logical URL (scheme, Host header,
    path and query), so a page read pinned to an IP still matches its host."""

    def __init__(self):
        self.routes = []
        self.requests = []

    def on(self, prefix, *answers):
        """Answer requests whose logical URL starts with prefix. Each answer is
        an httpx.Response, a (sync or async) callable(request) returning one, or
        an exception to raise; several are served in order, the last repeats."""
        self.routes.append((prefix, list(answers)))
        return self

    def page(self, url, body, ctype="text/html; charset=utf-8", status=200, headers=None):
        content = body.encode() if isinstance(body, str) else body
        return self.on(url, lambda _req: httpx.Response(
            status, content=content, headers={"content-type": ctype, **(headers or {})}))

    @staticmethod
    def logical(request):
        return f"{request.url.scheme}://{request.headers['host']}{request.url.raw_path.decode()}"

    def urls(self):
        return [self.logical(r) for r in self.requests]

    async def handler(self, request):
        self.requests.append(request)
        url = self.logical(request)
        # Longest prefix wins; among equal prefixes the latest registered.
        for prefix, answers in sorted(reversed(self.routes), key=lambda r: -len(r[0])):
            if url.startswith(prefix):
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                if isinstance(answer, BaseException):
                    raise answer
                if callable(answer):
                    answer = answer(request)
                    if not isinstance(answer, httpx.Response):
                        answer = await answer
                return answer
        raise httpx.ConnectError(f"no route for {url}", request=request)


class Dns:
    """getaddrinfo over a dict; numeric hosts (127.1, 2130706433) resolve to the
    address they spell, as the real resolver does; unknown names fail like
    NXDOMAIN."""

    def __init__(self):
        self.records = {}
        self.lookups = []

    def add(self, host, *ips):
        self.records[host] = list(ips)

    def getaddrinfo(self, host, port, *args, **kwargs):
        self.lookups.append(host)
        ips = self.records.get(host)
        if ips is None:
            try:
                ips = [socket.inet_ntoa(socket.inet_aton(host))]
            except OSError:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known") from None
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in ips
        ]


def _clear():
    engine._cache.clear()
    engine._page_cache.clear()
    engine._blocked_hosts.clear()
    excerpts._background.clear()
    ddg.reset_circuit()
    brave._brave_auth_until = 0.0
    policy._cached = None


@pytest.fixture(autouse=True)
def _search_state(monkeypatch):
    _clear()
    monkeypatch.setattr(ddg, "_warmed", True)
    monkeypatch.setattr(settings, "brave_search_api_key", "")
    monkeypatch.setattr(engine, "limiter", security.RateLimiter())
    # No domain lists unless a test stores some (fresh_db + save_domain_lists).
    monkeypatch.setattr(policy, "_cached", (math.inf, ([], [])))
    yield
    _clear()


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    resolver = Dns()
    monkeypatch.setattr(ssrf, "socket", SimpleNamespace(
        getaddrinfo=resolver.getaddrinfo, inet_aton=socket.inet_aton, SOCK_STREAM=socket.SOCK_STREAM))
    return resolver


@pytest.fixture(autouse=True)
def web(monkeypatch):
    scripted = Web()
    search = httpx.AsyncClient(transport=client.CappedTransport(inner=httpx.MockTransport(scripted.handler)),
                               follow_redirects=False)
    fetch = httpx.AsyncClient(transport=client.CappedTransport(PDF_MAX_BYTES + 1, inner=httpx.MockTransport(scripted.handler)),
                              follow_redirects=False, cookies=client._NoCookies())
    monkeypatch.setattr(client, "_search_client", search)
    monkeypatch.setattr(client, "_fetch_client", fetch)
    return scripted
