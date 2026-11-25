from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
import time
from urllib.parse import urljoin

import httpx

from .. import egress
from ..client import TIMEOUT_DDG, PROVIDER_BODY_MAX
from ..ssrf import decode_ddg_url, host_of, public_http_url
from .base import NextMatch, ProviderResponse, SearchHit, empty_reason, strip_html

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_SEARCH_HOSTS = {
    "html.duckduckgo.com",
    "lite.duckduckgo.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
}
# Linear on hostile pages (see base.strip_html): one <a ...> tag at a time,
# attributes looked up inside it, closing tags found by NextMatch.
_A_TAG = re.compile(r"<a\b[^<>]*>", re.I)
_A_CLOSE = re.compile(r"</a>", re.I)
_RESULT_A = re.compile(r'class="[^"]*result__a[^"]*"', re.I)
_HREF = re.compile(r'href="([^"]+)"', re.I)
_LITE_REL = re.compile(r"rel=[\"']nofollow[\"']", re.I)
_LITE_HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
_DDG_SNIP = re.compile(r'class="[^"]*result__snippet[^"]*"', re.I)
_DDG_SNIP_END = re.compile(r"</(?:a|span|td|div)", re.I)
_LITE_SNIP = re.compile(r"class=['\"]result-snippet['\"]", re.I)
_LITE_SNIP_END = re.compile(r"</td>", re.I)
_GT = re.compile(">")

# Keyed by egress.key(): DuckDuckGo judges the IP a request comes from, so
# one user's flagged machine must not pause the provider for anyone else.
_fail_streak: dict[str, int] = {}
_break_until: dict[str, float] = {}
_warmed: set[str] = set()
# DuckDuckGo answers flagged server IPs with a bot challenge (HTTP 202/403,
# "anomaly" page) on nearly every request; retrying each query twice only
# added load and latency. One challenge pauses the provider for a while.
CHALLENGE_BACKOFF = 600.0


def ddg_circuit_open() -> bool:
    return time.monotonic() < _break_until.get(egress.key(), 0.0)


def reset_circuit() -> None:
    _fail_streak.clear()
    _break_until.clear()


def _record_challenge() -> None:
    _fail_streak.pop(egress.key(), None)
    _break_until[egress.key()] = time.monotonic() + CHALLENGE_BACKOFF


def _record_timeout() -> None:
    key = egress.key()
    _fail_streak[key] = _fail_streak.get(key, 0) + 1
    if _fail_streak[key] >= 5:
        _break_until[key] = time.monotonic() + 30
        _fail_streak.pop(key)


def _record_ok() -> None:
    _fail_streak.pop(egress.key(), None)


_strip = strip_html


def _search_host_ok(url: str) -> bool:
    host = host_of(url)
    return host in _SEARCH_HOSTS or host.endswith(".duckduckgo.com")


def _snippets(raw: str, opener: re.Pattern, closer: re.Pattern) -> list[tuple[int, str]]:
    """(start, text) per opener ... >text closer, in order and never nested."""
    gt, close = NextMatch(raw, _GT), NextMatch(raw, closer)
    found: list[tuple[int, str]] = []
    end = 0
    for match in opener.finditer(raw):
        if match.start() < end:
            continue
        tag_end = gt(match.end())
        stop = close(tag_end.end()) if tag_end else None
        if not stop:
            break
        found.append((match.start(), _strip(raw[tag_end.end():stop.start()])))
        end = stop.end()
    return found


def _links(raw: str, href_of) -> list[tuple[int, str, str]]:
    """(start, href, title html) per <a> tag href_of accepts, never one
    inside the previous link's text."""
    close = NextMatch(raw, _A_CLOSE)
    found: list[tuple[int, str, str]] = []
    end = 0
    for tag in _A_TAG.finditer(raw):
        if tag.start() < end:
            continue
        href = href_of(tag.group(0))
        if not href:
            continue
        stop = close(tag.end())
        if not stop:
            break
        found.append((tag.start(), href, raw[tag.end():stop.start()]))
        end = stop.end()
    return found


def _result_href(tag: str) -> str:
    href = _HREF.search(tag) if _RESULT_A.search(tag) else None
    return href.group(1) if href else ""


def _lite_href(tag: str) -> str:
    href = _LITE_HREF.search(tag) if _LITE_REL.search(tag) else None
    return href.group(1) if href else ""


def _with_next_start(found: list[tuple[int, str, str]], raw: str):
    for n, link in enumerate(found):
        yield link, found[n + 1][0] if n + 1 < len(found) else len(raw)


def parse_ddg_html(raw: str) -> list[SearchHit]:
    raw = raw or ""
    if "anomaly" in raw.lower() and "unfortunate" in raw.lower():
        return []
    snips = _snippets(raw, _DDG_SNIP, _DDG_SNIP_END)
    if not snips:
        snips = _snippets(raw, _LITE_SNIP, _LITE_SNIP_END)
    results: list[SearchHit] = []
    seen: set[str] = set()

    def _snippet_after(pos: int, end: int) -> str:
        # Only before the next result's link: a result without a snippet
        # must not take its neighbour's.
        for start, text in snips:
            if pos <= start < end:
                return text
        return ""

    for (start, href, title), end in _with_next_start(_links(raw, _result_href), raw):
        title = _strip(title)
        url = decode_ddg_url(href)
        if not url or url in seen or not title:
            continue
        seen.add(url)
        snippet = _snippet_after(start, end)
        results.append(SearchHit(title=title[:200], url=url, snippet=snippet[:280], provider="ddg", rank=len(results)))
        if len(results) >= 8:
            break
    if not results:
        for (start, href, title), end in _with_next_start(_links(raw, _lite_href), raw):
            title = _strip(title)
            url = decode_ddg_url(href) or public_http_url(html_lib.unescape(href))
            if not url or url in seen or not title:
                continue
            seen.add(url)
            snippet = _snippet_after(start, end)
            results.append(
                SearchHit(title=title[:200], url=url, snippet=snippet[:280], provider="ddg", rank=len(results))
            )
            if len(results) >= 8:
                break
    return results


async def _warm(client: httpx.AsyncClient, timeout: httpx.Timeout) -> None:
    if egress.key() in _warmed:
        return
    try:
        await _request(client, "GET", "https://duckduckgo.com/", timeout, headers={"User-Agent": _UA, "Accept": "text/html"})
        _warmed.add(egress.key())
    except Exception:
        pass


async def _request(client: httpx.AsyncClient, method: str, url: str, timeout: httpx.Timeout, **kwargs) -> httpx.Response:
    current = url
    for _ in range(4):
        if not _search_host_ok(current):
            raise ValueError("blocked")
        resp = await client.request(method, current, follow_redirects=False, timeout=timeout, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308) or (
            resp.status_code == 202 and resp.headers.get("location")
        ):
            loc = resp.headers.get("location") or ""
            current = urljoin(current, loc)
            kwargs.pop("data", None)
            method = "GET"
            continue
        text = getattr(resp, "text", None)
        if isinstance(text, str):
            raw = text.encode("utf-8", "replace")[:PROVIDER_BODY_MAX]
        else:
            raw = bytes(getattr(resp, "content", b"") or b"")[:PROVIDER_BODY_MAX]
        return httpx.Response(
            status_code=resp.status_code,
            headers=resp.headers,
            content=raw,
            request=getattr(resp, "request", None),
        )
    raise ValueError("redirect")


class DdgProvider:
    name = "ddg"
    timeout = TIMEOUT_DDG

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        last_timeout = timeout
        if ddg_circuit_open():
            logger.error("fuse.search provider=ddg error=circuit")
            return ProviderResponse(hits=[], status=0, error="circuit", last_timeout=last_timeout)
        headers = {"User-Agent": _UA, "Accept": "text/html"}
        await _warm(client, timeout)
        df = ""
        if recency_days is not None and recency_days <= 1:
            df = "d"
        elif recency_days is not None and recency_days <= 7:
            df = "w"
        elif recency_days is not None and recency_days <= 31:
            df = "m"
        elif recency_days is not None:
            df = "y"
        params = {"q": query}
        if df:
            params["df"] = df
        attempts = (
            ("GET", "https://lite.duckduckgo.com/lite/", {"params": params, "headers": headers}),
            (
                "POST",
                "https://html.duckduckgo.com/html/",
                {
                    "data": {"q": query, "kl": "us-en", "df": df},
                    "headers": {**headers, "Content-Type": "application/x-www-form-urlencoded"},
                },
            ),
        )
        last_status = 0
        last_error = "http"
        for method, url, kw in attempts:
            try:
                resp = await _request(client, method, url, timeout, **kw)
            except httpx.TimeoutException:
                last_status, last_error = 0, "timeout"
                logger.error("fuse.search provider=ddg error=timeout method=%s", method)
                _record_timeout()
                continue
            except Exception as exc:
                last_status, last_error = 0, "http"
                logger.error("fuse.search provider=ddg error=%s method=%s", type(exc).__name__, method)
                continue
            last_status = resp.status_code
            if resp.status_code in (403, 429):
                last_error = "challenge"
                _record_challenge()
                logger.warning("fuse.search provider=ddg status=%s method=%s backoff=%ss", resp.status_code, method, int(CHALLENGE_BACKOFF))
                break
            if resp.status_code not in (200, 202):
                last_error = f"http_{resp.status_code}"
                logger.error("fuse.search provider=ddg status=%s method=%s", resp.status_code, method)
                continue
            found = await asyncio.to_thread(parse_ddg_html, resp.text)
            if found:
                _record_ok()
                return ProviderResponse(hits=found, status=200, error=None, last_timeout=last_timeout)
            last_error = empty_reason(resp.text, "result__a") if resp.status_code == 200 else "challenge"
            if last_error == "empty" and "anomaly" in (resp.text or "").lower():
                last_error = "blocked"
            if last_error in ("challenge", "blocked"):
                _record_challenge()
                logger.warning(
                    "fuse.search provider=ddg status=%s method=%s error=%s backoff=%ss",
                    resp.status_code, method, last_error, int(CHALLENGE_BACKOFF),
                )
                break
            logger.warning(
                "fuse.search provider=ddg status=%s method=%s error=%s body_len=%s",
                resp.status_code,
                method,
                last_error,
                len(resp.text or ""),
            )
        return ProviderResponse(hits=[], status=last_status, error=last_error, last_timeout=last_timeout)
