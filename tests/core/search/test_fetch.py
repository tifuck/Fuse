"""read_url backend: fetch_url's SSRF guard, redirects, domain policy, limits,
content types, error shapes, page cache and per-user read limits.

DNS is the fake resolver from conftest (``dns``) and every request goes to the
scripted transport (``web``), behind the real CappedTransport.
"""
import asyncio
import gzip
import sys
import tracemalloc

import httpcore
import httpx
import pytest

from app import skill_install
from app.search import client, engine, ssrf
from app.search.client import CappedTransport
from app.search.engine import fetch_url, is_fetch_cached, is_host_blocked
from app.search.extract import FETCH_CHARS
from app.search.policy import save_domain_lists

from ._support import Hang, run

ARTICLE = "<html><head><title>Doc</title></head><body><article><h1>Doc</h1><p>{}</p></article></body></html>"
TEXT = "The public document body has enough useful words to be read and extracted safely."


def page(body=TEXT):
    return ARTICLE.format(body)


def read(url, user_id=1, allowed=None, **kw):
    return run(fetch_url(url, user_id=user_id, allowed=allowed if allowed is not None else set(), **kw))


def public(dns, *hosts):
    for i, host in enumerate(hosts):
        dns.add(host, f"93.184.216.{10 + i}")


async def wire(data, size=4096):
    """data streamed like a socket. An encoded bytes body would be decoded by
    httpx.Response itself, before the transport under test sees it."""
    for at in range(0, len(data), size):
        yield data[at:at + size]


def chunks(size, count, consumed, piece=b"a"):
    """A streamed body of count chunks of about size bytes; consumed records
    how many the reader pulled."""
    async def gen():
        for i in range(count):
            consumed.append(i)
            yield piece * (size // len(piece))

    return gen()


# -- SSRF ---------------------------------------------------------------------


def test_a_read_is_pinned_to_the_vetted_ip_with_host_and_sni_kept(web, dns):
    dns.add("example.com", "93.184.216.34")
    dns.add("v6.example", "2606:4700::1111")
    web.page("https://example.com/doc?x=1", page())
    web.page("https://v6.example/doc", page())

    extract, wrapped, err = read("https://example.com/doc?x=1")

    assert not err and TEXT in extract.text
    assert wrapped.startswith("UNTRUSTED PAGE EXTRACT from https://example.com/doc?x=1")
    assert dns.lookups == ["example.com"], "resolved once"
    [req] = web.requests
    assert req.url.host == "93.184.216.34" and req.url.raw_path == b"/doc?x=1"
    assert req.headers["host"] == "example.com"
    assert req.extensions["sni_hostname"] == "example.com"
    assert "cookie" not in req.headers

    extract, _, err = read("https://v6.example/doc")
    assert not err and web.requests[-1].url.host == "2606:4700::1111"
    assert web.requests[-1].headers["host"] == "v6.example"


@pytest.mark.parametrize("url", [
    "http://example.com/plain-http",
    "ftp://example.com/file",
    "file:///etc/passwd",
    "gopher://example.com/",
    "https://localhost/admin",
    "https://LOCALHOST./admin",
    "https://app.localhost/",
    "https://127.0.0.1/",
    "https://127.1/",
    "https://2130706433/",
    "https://0x7f.1/",
    "https://10.0.0.5/",
    "https://192.168.1.1/",
    "https://100.64.0.1/",
    "https://0.0.0.0/",
    "https://169.254.169.254/latest/meta-data/",
    "https://metadata.google.internal/computeMetadata/v1/",
    "https://box.internal/",
    "https://printer.local/",
    "https://[::1]/",
    "https://[fd00::1]/",
    "https://[fe80::1]/",
    "https://[::ffff:127.0.0.1]/",
    "https://[::ffff:100.64.0.1]/",
    "https://[::7f00:1]/",
    "https://[fec0::1]/",
    "https://[64:ff9b::7f00:1]/",
    "https://[64:ff9b:1::5db8:d822]/",
    "https://[2002:7f00:1::]/",
    "https://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/",
    "https://ｌｏｃａｌｈｏｓｔ/",
    "https://user:secret@example.com/",
    "https://example.com:8443/",
])
def test_unsafe_urls_are_refused_before_any_lookup_or_request(web, dns, url):
    dns.add("example.com", "93.184.216.34")
    extract, wrapped, err = read(url)
    assert extract is None and err == wrapped == "That URL is not an allowed public HTTPS address."
    assert dns.lookups == [] and web.requests == []


@pytest.mark.parametrize("ips", [
    ["127.0.0.1"], ["10.1.2.3"], ["169.254.169.254"], ["::1"], ["::ffff:10.0.0.1"], ["fc00::5"], ["64:ff9b::a00:1"],
    ["93.184.216.34", "10.0.0.1"],  # one private answer taints the name
])
def test_names_resolving_to_non_public_addresses_are_refused(web, dns, ips):
    dns.add("rebind.example", *ips)
    extract, _, err = read("https://rebind.example/")
    assert extract is None and err == "Refused to fetch a non-public address."
    assert web.requests == []


def test_an_international_name_is_read_and_checked_in_its_ascii_form(web, dns):
    dns.add("xn--bcher-kva.example", "93.184.216.34")
    web.page("https://xn--bcher-kva.example/doc", page())
    extract, wrapped, err = read("https://bücher.example/doc")
    assert not err and TEXT in extract.text
    assert wrapped.startswith("UNTRUSTED PAGE EXTRACT from https://xn--bcher-kva.example/doc")
    [req] = web.requests
    assert req.headers["host"] == req.extensions["sni_hostname"] == "xn--bcher-kva.example"
    assert dns.lookups == ["xn--bcher-kva.example"]
    assert read("https://" + "é" * 70 + ".example/")[2] == "That URL is not an allowed public HTTPS address."


def test_numeric_hosts_resolve_to_the_address_they_spell(dns):
    assert ssrf.public_ips("127.1") == ssrf.public_ips("2130706433") == []
    assert ssrf.public_ips("1572395042") == ["93.184.216.34"]


def test_skill_downloads_connect_to_the_vetted_ip(web, dns, monkeypatch):
    capped = client.CappedTransport
    monkeypatch.setattr(client, "CappedTransport", lambda cap: capped(cap, inner=httpx.MockTransport(web.handler)))
    dns.add("api.github.com", "140.82.112.6")
    web.on("https://api.github.com/repos/x", httpx.Response(200, content=b"{}"))
    resp = run(skill_install._http_get("https://api.github.com/repos/x", hosts={"api.github.com"}))
    assert resp.status_code == 200
    [req] = web.requests
    assert req.url.host == "140.82.112.6" and req.headers["host"] == "api.github.com"
    assert req.extensions["sni_hostname"] == "api.github.com"
    assert dns.lookups == ["api.github.com"], "resolved once"


def test_an_unresolvable_name_is_refused_without_a_request(web):
    extract, _, err = read("https://nxdomain.example/")
    assert extract is None and err == "Refused to fetch a non-public address."
    assert web.requests == []


def test_redirects_are_resolved_and_checked_at_every_hop(web, dns):
    public(dns, "example.com", "docs.example.org")
    web.on("https://example.com/old", lambda _r: httpx.Response(302, headers={"location": "https://docs.example.org/new"}))
    web.on("https://docs.example.org/new", lambda _r: httpx.Response(301, headers={"location": "/final"}))
    web.page("https://docs.example.org/final", page())
    allowed = set()

    extract, wrapped, err = read("https://example.com/old", allowed=allowed)

    assert not err and TEXT in extract.text
    assert wrapped.startswith("UNTRUSTED PAGE EXTRACT from https://docs.example.org/final")
    assert dns.lookups == ["example.com", "docs.example.org", "docs.example.org"]
    assert [(r.url.host, r.headers["host"]) for r in web.requests] == [
        ("93.184.216.10", "example.com"), ("93.184.216.11", "docs.example.org"), ("93.184.216.11", "docs.example.org")]
    assert "https://docs.example.org/final" in allowed


@pytest.mark.parametrize("location, error", [
    ("http://example.com/plain", "Refused an unsafe redirect."),
    ("https://127.0.0.1/admin", "Refused an unsafe redirect."),
    ("https://169.254.169.254/latest/meta-data/", "Refused an unsafe redirect."),
    ("file:///etc/passwd", "Refused an unsafe redirect."),
    ("https://[bad", "Refused an unsafe redirect."),
    ("https://internal.example/admin", "Refused to fetch a non-public address."),
])
def test_unsafe_redirect_targets_are_refused(web, dns, location, error):
    public(dns, "example.com")
    dns.add("internal.example", "10.0.0.8")
    web.on("https://example.com/r", lambda _r: httpx.Response(302, headers={"location": location}))
    extract, _, err = read("https://example.com/r")
    assert extract is None and err == error
    assert len(web.requests) == 1, "the redirect target is never requested"


def test_a_redirect_loop_ends(web, dns):
    public(dns, "example.com")
    web.on("https://example.com/a", lambda _r: httpx.Response(302, headers={"location": "/b"}))
    web.on("https://example.com/b", lambda _r: httpx.Response(302, headers={"location": "/a"}))
    extract, _, err = read("https://example.com/a")
    assert extract is None and err == "Too many redirects."
    assert len(web.requests) == engine.FETCH_MAX_REDIRECTS + 1


def test_up_to_the_redirect_limit_is_followed(web, dns):
    public(dns, "example.com")
    for hop in range(engine.FETCH_MAX_REDIRECTS):
        web.on(f"https://example.com/r{hop}", lambda _r, nxt=hop + 1: httpx.Response(302, headers={"location": f"/r{nxt}"}))
    web.page(f"https://example.com/r{engine.FETCH_MAX_REDIRECTS}", page())
    extract, _, err = read("https://example.com/r0")
    assert not err and TEXT in extract.text


def test_domain_blacklist_and_whitelist_apply_to_urls_redirects_and_cached_pages(fresh_db, web, dns):
    public(dns, "good.example", "other.example", "sub.blocked.example", "blocked.example")
    web.on("https://good.example/moved", lambda _r: httpx.Response(302, headers={"location": "https://other.example/page"}))
    web.page("https://other.example/page", page())
    web.page("https://blocked.example/", page())
    assert not read("https://good.example/moved")[2]

    save_domain_lists(["*.blocked.example", "other.example"], [])
    requests = len(web.requests)
    for url in ("https://sub.blocked.example/", "https://other.example/page"):
        extract, _, err = read(url)
        assert extract is None and err == "That domain is not allowed."
    assert not read("https://blocked.example/")[2], "*.suffix matches subdomains, not the apex"
    extract, _, err = read("https://good.example/moved")
    assert extract is None and err == "Refused a non-HTTPS or blocked redirect.", \
        "a cached page whose redirect target is now blocked is fetched again and refused"
    assert len(web.requests) == requests + 2

    save_domain_lists([], ["good.example"])
    assert read("https://blocked.example/")[2] == "That domain is not allowed."


# -- limits and content types -------------------------------------------------------


def test_a_page_over_the_size_cap_is_cut_and_said_to_be_cut(web, dns, monkeypatch):
    monkeypatch.setattr(engine, "FETCH_MAX_BYTES", 100_000)
    public(dns, "big.example")
    consumed = []
    web.on("https://big.example/", lambda _r: httpx.Response(
        200, headers={"content-type": "text/plain"}, content=chunks(70_000, 50, consumed, b"word ")))

    extract, wrapped, err = read("https://big.example/")

    assert not err and extract.download_truncated and extract.truncated
    assert "the download stopped at the size cap" in wrapped
    assert extract.total_chars <= 100_000
    assert len(consumed) < 50, "the rest of the body is not downloaded"


def test_a_byte_order_mark_wins_over_the_header_charset(web, dns):
    public(dns, "bom.example")
    body = page("Le café ouvre à huit heures, avec assez de mots pour être lu en entier.")
    web.page("https://bom.example/utf8", b"\xef\xbb\xbf" + body.encode(), ctype="text/html; charset=iso-8859-1")
    web.page("https://bom.example/utf16", "\ufeffcafé crème au lait".encode("utf-16-be"), ctype="text/plain; charset=utf-8")
    assert "Le café ouvre à huit heures" in read("https://bom.example/utf8")[0].text
    assert "café crème au lait" in read("https://bom.example/utf16")[0].text


def test_compressed_pages_are_inflated_within_a_cap_and_unknown_encodings_refused(web, dns):
    public(dns, "gz.example", "br.example")
    web.on("https://gz.example/", lambda _r: httpx.Response(
        200, headers={"content-type": "text/html", "content-encoding": "gzip"}, content=wire(gzip.compress(page().encode()))))
    web.on("https://br.example/", lambda _r: httpx.Response(
        200, headers={"content-type": "text/html", "content-encoding": "br"}, content=wire(b"\x00\x01")))

    extract, _, err = read("https://gz.example/")
    assert not err and TEXT in extract.text
    extract, _, err = read("https://br.example/")
    assert extract is None and err == "The page's compression could not be decoded."


def test_capped_transport_never_inflates_a_compression_bomb_past_its_cap():
    bomb = gzip.compress(b"\0" * 20_000_000)  # ~20 KB on the wire

    async def body():
        transport = CappedTransport(50_000, inner=httpx.MockTransport(
            lambda _r: httpx.Response(200, headers={"content-encoding": "gzip"}, content=wire(bomb))))
        async with httpx.AsyncClient(transport=transport) as c:
            return (await c.get("https://bomb.example/")).content

    tracemalloc.start()
    try:
        content = run(body())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(content) <= 50_000
    assert peak < 5_000_000, "the inflated body never sits in memory"


def test_unreadable_types_are_refused_and_untyped_bodies_are_sniffed(web, dns):
    public(dns, "files.example")
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    web.page("https://files.example/report", b"PK\x03\x04", ctype=docx)
    web.page("https://files.example/image", b"\x89PNG", ctype="image/png")
    consumed = []
    web.on("https://files.example/archive", lambda _r: httpx.Response(
        200, headers={"content-type": "application/octet-stream"}, content=chunks(70_000, 50, consumed, b"PK\x03\x04\x00")))
    web.page("https://files.example/notes", "plain notes that were served untyped", ctype="application/octet-stream")
    web.page("https://files.example/data", '{"ok": true, "n": 1}', ctype="application/json")

    assert read("https://files.example/report")[2].startswith("That page is not readable text (application/vnd.")
    assert read("https://files.example/image")[2] == "That page is not readable text (image/png)."
    assert read("https://files.example/archive")[2] == "That page is not readable text (binary file)."
    assert consumed == [0], "a binary is refused on its first chunk"
    extract, _, err = read("https://files.example/notes")
    assert not err and extract.kind == "text" and "plain notes" in extract.text
    extract, _, err = read("https://files.example/data")
    assert not err and extract.kind == "json" and '"ok": true' in extract.text


def test_pdfs_are_extracted_off_process_and_oversized_ones_refused(web, dns, monkeypatch):
    seen = []

    async def worker(raw):
        seen.append(raw)
        return "Manual text for the 811LM remote."

    monkeypatch.setattr(engine, "_pdf_worker", worker)
    public(dns, "files.example")
    web.page("https://files.example/manual", b"%PDF-1.7 tiny", ctype="application/octet-stream")
    extract, wrapped, err = read("https://files.example/manual")
    assert not err and "811LM remote" in wrapped and seen == [b"%PDF-1.7 tiny"]

    monkeypatch.setattr(engine, "PDF_MAX_BYTES", 1_000)
    web.page("https://files.example/huge.pdf", b"%PDF-1.7" + b"x" * 5_000, ctype="application/pdf")
    extract, _, err = read("https://files.example/huge.pdf")
    assert extract is None and err.startswith("The PDF is larger than")
    assert len(seen) == 1, "an oversized PDF is never parsed"


def test_pdf_extraction_runs_at_most_two_workers_at_once(monkeypatch):
    running = []
    peak = []

    async def worker(_raw):
        running.append(1)
        peak.append(len(running))
        await asyncio.sleep(0)  # let every queued call try to start
        running.pop()
        return "text"

    async def burst():
        monkeypatch.setattr(engine, "_PDF_SLOTS", asyncio.Semaphore(2))
        return await asyncio.gather(*(engine._pdf_text(b"%PDF") for _ in range(6)))

    monkeypatch.setattr(engine, "_pdf_worker", worker)
    assert run(burst()) == ["text"] * 6
    assert max(peak) == 2


def test_markdown_is_asked_for_first_and_a_refusal_is_retried_as_a_browser(web, dns):
    public(dns, "docs.example")
    web.on("https://docs.example/models",
           lambda _r: httpx.Response(406, headers={"content-type": "text/html"}),
           lambda _r: httpx.Response(200, text=page(), headers={"content-type": "text/html"}))
    web.page("https://docs.example/models.md", "# Models\n\n| Model | Price |\n| --- | --- |\n| m1 | $2 |",
             ctype="text/markdown")

    extract, _, err = read("https://docs.example/models")
    assert not err and TEXT in extract.text
    first, second = web.requests
    assert first.headers["accept"].startswith("text/markdown") and "gzip" in first.headers["accept-encoding"]
    assert second.headers["accept"] == engine.BROWSER_ACCEPT

    extract, wrapped, err = read("https://docs.example/models.md")
    assert web.requests[-1].headers["accept"] == engine.BROWSER_ACCEPT, "a URL naming a file is read as a browser would"
    assert "| m1 | $2 |" in wrapped and [t for _, _, t in extract.outline] == ["Models"]


# -- error shapes ------------------------------------------------------------------


def test_http_and_network_failures_have_clear_messages(web, dns):
    public(dns, "a.example", "walled.example", "b.example", "c.example", "d.example", "e.example")
    web.page("https://a.example/gone", "", status=404)
    web.page("https://walled.example/page", "denied", status=403)
    web.page("https://b.example/err", "oops", status=500)
    web.on("https://c.example/", httpx.ReadTimeout("slow"))
    web.on("https://d.example/", httpx.ConnectError("refused"))
    web.on("https://e.example/", httpx.RemoteProtocolError("eof"))

    assert read("https://a.example/gone")[2] == "Page returned HTTP 404 (not found)."
    err = read("https://walled.example/page")[2]
    assert err.startswith("Page returned HTTP 403: walled.example blocks automated readers.")
    assert is_host_blocked("https://walled.example/other"), "a bot wall is remembered for search-time reads"
    assert read("https://b.example/err")[2] == "Page returned HTTP 500."
    assert not is_host_blocked("https://b.example/")
    assert read("https://c.example/")[2].startswith("Page fetch timed out: c.example")
    assert read("https://d.example/")[2] == "Could not connect to d.example (DNS, TLS or network failure)."
    assert read("https://e.example/")[2] == "e.example dropped the connection before the page finished."


def test_a_server_that_never_finishes_hits_the_read_deadline(web, dns, monkeypatch):
    monkeypatch.setattr(engine, "FETCH_DEADLINE", 0.05)
    public(dns, "slow.example")
    stall = Hang()
    web.on("https://slow.example/", stall)
    extract, _, err = read("https://slow.example/")
    assert extract is None and err.startswith("Page fetch timed out")
    assert stall.cancelled == 1


def test_a_page_without_text_is_an_error_and_a_script_shell_is_flagged(web, dns):
    public(dns, "empty.example", "app.example")
    web.page("https://empty.example/", " \n\t\n ", ctype="text/plain")
    web.page("https://app.example/", "<html><head><title>App</title><meta property='og:description' content='A chart app'>"
                                     "</head><body><div id='root'></div><script>var bundle=1</script></body></html>")
    assert read("https://empty.example/")[2] == "The page had no extractable text."
    extract, wrapped, err = read("https://app.example/")
    assert not err and extract.kind == "spa"
    assert "JavaScript app" in wrapped and "do not invent" in wrapped and "bundle" not in wrapped


def test_an_interrupted_read_raises_without_a_request(web, dns):
    public(dns, "example.com")
    web.page("https://example.com/", page())
    cancel = asyncio.Event()
    cancel.set()

    async def attempt():
        with pytest.raises(asyncio.CancelledError):
            await fetch_url("https://example.com/", user_id=1, allowed=set(), cancel=cancel)

    run(attempt())
    assert web.requests == []


# -- page cache, links and read limits -------------------------------------------------


def test_cached_pages_serve_find_query_and_offset_reads_without_the_network(web, dns, monkeypatch):
    monkeypatch.setattr(engine, "FETCH_PER_MIN", 1)
    public(dns, "docs.example")
    body = "".join(f"<h2>Part {i}</h2><p>{'filler words ' * 300}</p>" for i in range(10))
    body += "<h2>Limits</h2><p>The deep-cache-marker sits in the final section.</p>"
    web.page("https://docs.example/guide", ARTICLE.format(body))

    first, wrapped, err = read("https://docs.example/guide")
    assert not err and first.next_offset == FETCH_CHARS and "deep-cache-marker" not in first.text
    assert f"Call read_url again with offset={FETCH_CHARS}" in wrapped
    assert is_fetch_cached("https://docs.example/guide")

    found, _, err = read("https://docs.example/guide", find="deep-cache-marker")
    assert not err and found.used_find and "deep-cache-marker" in found.text
    ranked, _, err = read("https://docs.example/guide", query="limits section")
    assert not err and "deep-cache-marker" in ranked.text
    rest, _, err = read("https://docs.example/guide", offset=first.next_offset)
    assert not err and rest.offset == FETCH_CHARS
    assert first.text + rest.text == first.full_text[:len(first.text) + len(rest.text)]
    assert len(web.requests) == 1, "every later read came from the page cache, and spent no read budget"


def test_find_query_and_links_work_on_a_page_too_large_to_window_on_the_loop(web, dns):
    public(dns, "docs.example")
    body = "".join(f'<h2>Part {i}</h2><p>{"filler words " * 500} <a href="https://docs.example/p{i}">p{i}</a></p>'
                   for i in range(10))
    body += "<h2>Limits</h2><p>The deep-cache-marker sits in the final section.</p>"
    web.page("https://docs.example/guide", ARTICLE.format(body))

    allowed = set()
    first, _, err = read("https://docs.example/guide", allowed=allowed, find="deep-cache-marker")
    assert not err and len(first.full_text) > 50_000
    assert first.used_find and "deep-cache-marker" in first.text
    assert {"https://docs.example/guide", "https://docs.example/p3"} <= allowed
    ranked, _, err = read("https://docs.example/guide", query="limits section")
    assert not err and "deep-cache-marker" in ranked.text
    allowed = set()
    rest, _, err = read("https://docs.example/guide", allowed=allowed, offset=FETCH_CHARS)
    assert not err and rest.offset == FETCH_CHARS and rest.text == first.full_text[FETCH_CHARS:2 * FETCH_CHARS]
    assert "https://docs.example/p3" in allowed  # a cached read allows the page's links too
    assert len(web.requests) == 1


def test_a_redirected_page_is_cached_under_both_urls_but_counted_once(web, dns, monkeypatch):
    public(dns, "docs.example")
    web.on("https://docs.example/old", lambda _r: httpx.Response(301, headers={"location": "/new"}))
    web.page("https://docs.example/new", page("words " * 2_000))
    first, _, _ = read("https://docs.example/old")
    size = sys.getsizeof(first.full_text) + sys.getsizeof(first.text)
    engine._page_cache.clear()

    monkeypatch.setattr(engine._page_cache, "max_bytes", size + size // 2)
    extract, _, err = read("https://docs.example/old")
    assert not err and is_fetch_cached("https://docs.example/old") and is_fetch_cached("https://docs.example/new")
    requests = len(web.requests)
    assert not read("https://docs.example/old")[2] and not read("https://docs.example/new")[2]
    assert len(web.requests) == requests, "both URLs are served from the one cached copy"


def test_page_reads_are_rate_limited_per_user_with_a_separate_prefetch_budget(web, dns, monkeypatch):
    monkeypatch.setattr(engine, "FETCH_PER_MIN", 1)
    monkeypatch.setattr(engine, "PREFETCH_PER_MIN", 1)
    public(dns, "a.example", "b.example", "c.example")
    for host in ("a", "b", "c"):
        web.page(f"https://{host}.example/", page())

    assert not read("https://a.example/", user_id=1)[2]
    extract, _, err = read("https://b.example/", user_id=1)
    assert extract is None and err.startswith("Page-read rate limit reached (shared by all your chats and agents).")
    assert not read("https://b.example/", user_id=1, rate_kind="prefetch")[2], "search-time reads have their own budget"
    assert read("https://c.example/", user_id=1, rate_kind="prefetch")[2].startswith("Page-read rate limit")
    assert not read("https://c.example/", user_id=2)[2], "another user has their own budget"


def test_only_public_https_links_of_a_page_become_readable(web, dns):
    public(dns, "example.com")
    web.page("https://example.com/index", ARTICLE.format(
        TEXT + " <a href='/next'>Next page</a> <a href='https://localhost/private'>Private</a>"
        " <a href='http://plain.example/x'>Plain</a> <a href='https://169.254.169.254/'>Meta</a>"
        " <a href='https://other.example/doc#frag'>Other</a>"))
    allowed = set()
    assert not read("https://example.com/index", allowed=allowed)[2]
    assert allowed == {"https://example.com/index", "https://example.com/next", "https://other.example/doc"}


# -- site adapters -------------------------------------------------------------------


def test_stackexchange_questions_are_read_through_the_api(web, dns):
    public(dns, "stackoverflow.com", "api.stackexchange.com")
    question = {"items": [{"title": "Rust request with timeout", "score": 3, "answer_count": 2, "tags": ["rust"],
                           "accepted_answer_id": 9, "body": "<p>How do I set a timeout?</p>"}]}
    answers = {"items": [{"answer_id": 8, "score": 7, "body": "<p>Higher but not accepted.</p>"},
                         {"answer_id": 9, "score": 5, "body": "<pre><code>client.timeout(Duration::from_secs(5))</code></pre>"}]}
    web.on("https://api.stackexchange.com/2.3/questions/76196208?", lambda _r: httpx.Response(200, json=question))
    web.on("https://api.stackexchange.com/2.3/questions/76196208/answers", lambda _r: httpx.Response(200, json=answers))

    _, wrapped, err = read("https://stackoverflow.com/questions/76196208/rust-request-with-timeout")

    assert not err and "How do I set a timeout?" in wrapped
    assert "client.timeout(Duration::from_secs(5))" in wrapped
    assert wrapped.index("Accepted answer (score 5)") < wrapped.index("Answer (score 7)")
    assert not any(r.headers["host"] == "stackoverflow.com" for r in web.requests)


def test_github_files_read_through_raw_extract_pdfs_and_report_truncation(web, dns, monkeypatch):
    async def worker(raw):
        return "Datasheet text for the 811LM remote."

    monkeypatch.setattr(engine, "_pdf_worker", worker)
    monkeypatch.setattr(engine, "FETCH_MAX_BYTES", 100_000)
    public(dns, "github.com", "raw.githubusercontent.com")
    web.page("https://raw.githubusercontent.com/o/r/main/sheet.pdf", b"%PDF-1.7 tiny", ctype="application/octet-stream")
    web.page("https://raw.githubusercontent.com/o/r/main/big.txt", "word " * 30_000, ctype="text/plain")

    extract, wrapped, err = read("https://github.com/o/r/blob/main/sheet.pdf")
    assert not err and "811LM remote" in wrapped and "%PDF" not in wrapped
    extract, wrapped, err = read("https://github.com/o/r/blob/main/big.txt")
    assert not err and extract.download_truncated and "the download stopped at the size cap" in wrapped
    assert not any(r.headers["host"] == "github.com" for r in web.requests)


def test_a_failed_adapter_falls_back_to_the_page_itself(web, dns):
    public(dns, "github.com", "raw.githubusercontent.com")
    web.page("https://raw.githubusercontent.com/o/r/main/README.md", "", status=404, ctype="text/plain")
    web.page("https://github.com/o/r/blob/main/README.md", page("GitHub rendered file view text for the readme."))
    _, wrapped, err = read("https://github.com/o/r/blob/main/README.md")
    assert not err and "GitHub rendered file view text" in wrapped

class _CountedBackend(httpcore.AsyncMockBackend):
    """Two scripted HTTP/1.1 answers per connection; counts connects."""

    def __init__(self):
        super().__init__([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"] * 2)
        self.connects = 0

    async def connect_tcp(self, *args, **kwargs):
        self.connects += 1
        return await super().connect_tcp(*args, **kwargs)


def _connects_for_two_reads(make):
    async def body():
        backend = _CountedBackend()
        async with make() as c:
            c._transport._inner._pool._network_backend = backend
            for _ in range(2):
                resp = await c.get("http://93.184.216.34/doc", headers={"host": "example.com"})
                assert resp.text == "ok"
        return backend.connects

    return run(body())


def test_ip_pinned_page_reads_never_reuse_a_connection():
    # Pinned to an IP, a kept-alive connection would serve another site behind
    # the same CDN address (HTTP 421); the search client may keep its pool.
    assert _connects_for_two_reads(client._new_fetch_client) == 2
    assert _connects_for_two_reads(lambda: client._new_client(cookies=httpx.Cookies())) == 1
