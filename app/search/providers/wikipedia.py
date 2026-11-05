from __future__ import annotations

import html as html_lib
import re
import httpx

from ..client import TIMEOUT_WIKI
from ..ssrf import host_of, public_http_url, wiki_page_url
from .base import ProviderResponse, SearchHit, read_json, safe_get

_UA = "FuseChat/1.0 (dashboard search for signed-in users)"
_SEARCH_HOSTS = {"en.wikipedia.org"}
_TAG = re.compile(r"(?is)<[^>]+>")


def _search_host_ok(url: str) -> bool:
    host = host_of(url)
    return host in _SEARCH_HOSTS or host.endswith(".wikipedia.org")


def _strip(text: str) -> str:
    text = html_lib.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


class WikipediaProvider:
    name = "wikipedia"
    timeout = TIMEOUT_WIKI

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        del recency_days
        last_timeout = timeout
        url = "https://en.wikipedia.org/w/api.php"
        try:
            resp = await safe_get(
                client,
                url,
                host_ok=_search_host_ok,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 3,
                    "srprop": "snippet",
                    "format": "json",
                    "utf8": 1,
                },
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=timeout,
            )
        except httpx.TimeoutException:
            return ProviderResponse(hits=[], status=0, error="timeout", last_timeout=last_timeout)
        except httpx.HTTPError:
            return ProviderResponse(hits=[], status=0, error="http", last_timeout=last_timeout)
        except ValueError:
            # A redirect off Wikipedia, or a loop.
            return ProviderResponse(hits=[], status=0, error="redirect", last_timeout=last_timeout)
        if resp.status_code != 200:
            return ProviderResponse(hits=[], status=resp.status_code, error="http", last_timeout=last_timeout)
        try:
            data = await read_json(resp)
        except ValueError:
            return ProviderResponse(hits=[], status=resp.status_code, error="parse", last_timeout=last_timeout)
        rows = ((data.get("query") or {}).get("search") or []) if isinstance(data, dict) else []
        hits: list[SearchHit] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or not row.get("title"):
                continue
            title = str(row["title"])[:200]
            page = wiki_page_url(title)
            if not page:
                continue
            hits.append(
                SearchHit(
                    title=title,
                    url=page,
                    snippet=_strip(str(row.get("snippet") or ""))[:280],
                    provider="wikipedia",
                    rank=i,
                    query=query,
                )
            )
        return ProviderResponse(hits=hits, status=200, error=None if hits else "empty", last_timeout=last_timeout)
