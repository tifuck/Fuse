"""Search area helpers: scripted search providers and result builders."""
import asyncio

from app.search import engine
from app.search.providers.base import ProviderResponse, QueryResult, SearchHit

PROVIDERS = {"ddg": "_ddg", "bing": "_bing", "yahoo": "_yahoo", "hn": "_hn", "wikipedia": "_wiki", "quotes": "_quotes"}
EMPTY = ProviderResponse(hits=[], status=200, error="empty")


def run(coro):
    return asyncio.run(coro)


def hit(url, title="Result", snippet="", provider="ddg", rank=0, **kw):
    return SearchHit(title=title, url=url, snippet=snippet, provider=provider, rank=rank, **kw)


def result(query, hits):
    return QueryResult(query, hits, None, "ok", "mix", 1, False, 200)


class Hang:
    """A provider call that never answers; counts starts and cancellations."""

    def __init__(self):
        self.started = 0
        self.cancelled = 0

    async def __call__(self, query):
        self.started += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class Engines:
    """Replace every keyless provider's search with a scripted one.

    A behaviour is a list of SearchHit (answered with status 200), a
    ProviderResponse, an exception to raise, or an async callable(query).
    Providers not named answer a genuine empty results page. ``calls`` maps
    each provider to the queries it was asked.
    """

    def __init__(self, monkeypatch, **script):
        self.calls = {name: [] for name in PROVIDERS}
        self.recency = {name: [] for name in PROVIDERS}
        for name, attr in PROVIDERS.items():
            monkeypatch.setattr(getattr(engine, attr), "search", self._fake(name, script.get(name, EMPTY)))

    def _fake(self, name, behaviour):
        async def search(_client, query, *, recency_days, timeout):
            self.calls[name].append(query)
            self.recency[name].append(recency_days)
            if isinstance(behaviour, BaseException):
                raise behaviour
            if isinstance(behaviour, ProviderResponse):
                return behaviour
            if isinstance(behaviour, list):
                return ProviderResponse(hits=list(behaviour), status=200, error=None if behaviour else "empty")
            return await behaviour(query)

        return search

    def asked(self):
        return {name for name, queries in self.calls.items() if queries}
