from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import unquote

import httpx

from ..client import read_limited
from ..ssrf import host_of, public_http_url
from .base import ProviderResponse, SearchHit, element_html, empty_reason, safe_get, strip_html

logger = logging.getLogger(__name__)


def _yahoo_host_ok(url: str) -> bool:
    host = host_of(url)
    return host == "yahoo.com" or host.endswith(".yahoo.com")

TIMEOUT_YAHOO = httpx.Timeout(3.5, connect=1.5)
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_BLOCK = re.compile(r'class="[^"]*algo-sr[^"]*"(.*?)(?=class="[^"]*algo-sr|$)', re.I | re.S)
_RU = re.compile(r"RU=(https%3[aA]%2[fF]%2[fF][^/\s\"']+)")
# Openers and closers for base.element_html (linear on hostile pages).
_TITLE = re.compile(r"<h3", re.I)
_TITLE_END = re.compile(r"</h3>", re.I)
_SNIP = re.compile(r"<p class=\"fc-dustygray[^\"]*\"", re.I)
_SNIP_END = re.compile(r"</p>", re.I)

_strip = strip_html


def parse_yahoo_html(raw: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in _BLOCK.findall(raw or ""):
        title = ""
        t = element_html(block, _TITLE, _TITLE_END)
        if t is not None:
            title = _strip(t)
        url = ""
        ru = _RU.search(block)
        if ru:
            url = public_http_url(unquote(ru.group(1)))
        if not url or not title or url in seen:
            continue
        if _yahoo_host_ok(url):
            continue
        seen.add(url)
        sn = element_html(block, _SNIP, _SNIP_END)
        snippet = _strip(sn)[:280] if sn is not None else ""
        hits.append(SearchHit(title=title[:200], url=url, snippet=snippet, provider="yahoo", rank=len(hits)))
        if len(hits) >= 8:
            break
    return hits


class YahooProvider:
    name = "yahoo"
    timeout = TIMEOUT_YAHOO

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        params = {"p": query}
        if recency_days is not None and recency_days <= 1:
            params["fr2"] = "time"
            params["age"] = "1d"
        elif recency_days is not None and recency_days <= 7:
            params["fr2"] = "time"
            params["age"] = "1w"
        elif recency_days is not None and recency_days <= 31:
            params["fr2"] = "time"
            params["age"] = "1m"
        # Yahoo offers no wider age filter: longer windows search all dates.
        try:
            for attempt in range(2):
                resp = await safe_get(
                    client,
                    "https://search.yahoo.com/search",
                    host_ok=_yahoo_host_ok,
                    params=params,
                    headers={"User-Agent": _UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"},
                    timeout=timeout,
                )
                # Yahoo answers a few percent of requests with a transient 500.
                if resp.status_code < 500 or attempt:
                    break
            if resp.status_code != 200:
                logger.error("fuse.search provider=yahoo status=%s", resp.status_code)
                return ProviderResponse(hits=[], status=resp.status_code, error="http", last_timeout=timeout)
            body = await read_limited(resp)
        except httpx.TimeoutException:
            logger.error("fuse.search provider=yahoo error=timeout")
            return ProviderResponse(hits=[], status=0, error="timeout", last_timeout=timeout)
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("fuse.search provider=yahoo error=%s", type(exc).__name__)
            return ProviderResponse(hits=[], status=0, error="http", last_timeout=timeout)
        hits = await asyncio.to_thread(parse_yahoo_html, body)
        error = None if hits else empty_reason(body, "algo-sr")
        if error:
            logger.warning("fuse.search provider=yahoo status=200 error=%s body_len=%s", error, len(body or ""))
        return ProviderResponse(hits=hits, status=200, error=error, last_timeout=timeout)
