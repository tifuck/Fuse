"""SSRF guard (app.search.ssrf) as pure functions: host blocking, URL
cleaning, domain-list parsing and matching, DNS vetting and pinning.

Every outward fetch (read_url, search results, skill downloads, monitors,
SSH hosts) trusts these answers, so the cases aim at inputs that dress a
private address up as a public one. Contract: an unsafe or malformed input
is refused ('' / [] / True for "blocked"), never raised.

DNS is the fake resolver from conftest (``dns``).
"""
import socket

import pytest

from app.search import ssrf


# -- is_blocked_host ------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "", None, "localhost", "LOCALHOST.", "metadata", "metadata.google.internal",
    "ip6-localhost", "printer.local", "db.internal", "app.localhost",
    "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "100.64.0.1",
    "169.254.169.254", "0.0.0.0", "255.255.255.255",
])
def test_is_blocked_host_blocks_names_and_private_v4(host):
    assert ssrf.is_blocked_host(host)


@pytest.mark.parametrize("host", ["127.1", "2130706433", "0x7f.1", "0177.0.0.1", "0", "0X7F000001"])
def test_is_blocked_host_blocks_inet_aton_spellings_of_loopback(host):
    assert ssrf.is_blocked_host(host)


@pytest.mark.parametrize("host", [
    "::1", "::", "fe80::1", "fe80::1%eth0", "fc00::1", "fec0::1",
    "::ffff:127.0.0.1", "::ffff:100.64.0.1",  # mapped
    "64:ff9b::7f00:1", "64:ff9b:1::1",        # NAT64
    "2002:7f00:1::",                           # 6to4 of 127.0.0.1
    "::7f00:1",                                # IPv4-compatible
])
def test_is_blocked_host_blocks_v6_that_reaches_private_v4_or_is_local(host):
    assert ssrf.is_blocked_host(host)


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "2606:4700::1111", "1.1.1.1.", "bücher.de", "256.0.0.1"])
def test_is_blocked_host_allows_public_hosts_and_plain_names(host):
    assert not ssrf.is_blocked_host(host)


def test_is_blocked_host_refuses_a_host_with_a_nul_byte():
    assert ssrf.is_blocked_host("exa\x00mple.com")


# -- public_http_url --------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "", None, "   ", "example.com", "ftp://example.com/", "javascript:alert(1)",
    "file:///etc/passwd", "http:///path", "https://u@example.com/", "https://u:p@example.com/",
    "https://example.com\\@127.0.0.1/", "https://example.com:8080/", "https://example.com:0/",
    "https://example.com:99999/", "https://example.com:-1/", "https://[::1]/",
    "https://[::ffff:127.0.0.1]/", "http://127.1/", "http://localhost./",
])
def test_public_http_url_returns_empty_for_unsafe_or_malformed(url):
    assert ssrf.public_http_url(url) == ""


@pytest.mark.parametrize("url", ["http://１２７.０.０.１/", "http://127。0。0。1/", "http://ｌｏｃａｌｈｏｓｔ/"])
def test_public_http_url_refuses_unicode_spellings_of_local_hosts(url):
    assert ssrf.public_http_url(url) == ""


def test_public_http_url_accepts_exactly_2000_chars():
    base = "https://example.com/"
    url = base + "a" * (2000 - len(base))
    assert ssrf.public_http_url(url) == url


def test_public_http_url_returns_empty_over_2000_chars():
    base = "https://example.com/"
    assert ssrf.public_http_url(base + "a" * (2001 - len(base))) == ""


def test_public_http_url_https_only_refuses_http():
    assert ssrf.public_http_url("http://example.com/", https_only=True) == ""
    assert ssrf.public_http_url("https://example.com/", https_only=True) == "https://example.com/"


@pytest.mark.parametrize("raw, clean", [
    ("  HTTPS://EXAMPLE.com:443/a?b=1#frag  ", "https://example.com/a?b=1"),
    ("https://example.com", "https://example.com/"),
    ("http://example.com:80/x", "http://example.com/x"),
    ("https://a.example.com./", "https://a.example.com/"),
    ("https://example.com/;p?q", "https://example.com/;p?q"),
    ("https://bücher.de/x", "https://xn--bcher-kva.de/x"),
    ("https://[2606:4700::1111]:443/x?y=1", "https://[2606:4700::1111]/x?y=1"),
])
def test_public_http_url_normalizes_valid_urls(raw, clean):
    assert ssrf.public_http_url(raw) == clean


@pytest.mark.parametrize("url", [
    "HTTPS://EXAMPLE.com:443/a?b=1#f", "https://bücher.de/x", "http://example.com:443",
    "https://[2606:4700::1111]/", "https://example.com/;p?q",
])
def test_public_http_url_is_idempotent(url):
    once = ssrf.public_http_url(url)
    assert once and ssrf.public_http_url(once) == once


def test_public_http_url_returns_empty_for_a_host_with_a_nul_byte():
    assert ssrf.public_http_url("http://exa\x00mple.com/") == ""


# -- decode_ddg_url ---------------------------------------------------------------

def test_decode_ddg_url_unwraps_the_redirect_and_decodes_once():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%252Fb&rut=x"
    assert ssrf.decode_ddg_url(href) == "https://example.com/a%2Fb"


def test_decode_ddg_url_unwraps_on_a_duckduckgo_subdomain():
    assert ssrf.decode_ddg_url("https://html.duckduckgo.com/l/?uddg=https%3A%2F%2Fex.com%2F") == "https://ex.com/"


def test_decode_ddg_url_refuses_a_redirect_to_a_private_host():
    assert ssrf.decode_ddg_url("https://duckduckgo.com/l/?uddg=http%3A%2F%2F127.0.0.1%2F") == ""


def test_decode_ddg_url_does_not_unwrap_a_lookalike_domain():
    href = "https://evilduckduckgo.com/l/?uddg=https%3A%2F%2Fex.com%2F"
    assert ssrf.decode_ddg_url(href) == href


def test_decode_ddg_url_unescapes_html_entities():
    assert ssrf.decode_ddg_url("https://example.com/?a=1&amp;b=2") == "https://example.com/?a=1&b=2"


@pytest.mark.parametrize("href", ["", None, "&lt;x", "/relative/path"])
def test_decode_ddg_url_returns_empty_for_non_urls(href):
    assert ssrf.decode_ddg_url(href) == ""


# -- normalize_url ----------------------------------------------------------------

@pytest.mark.parametrize("raw, clean", [
    ("HTTPS://Ex.com:443/a#f", "https://ex.com/a"),
    ("//ex.com/p", "https://ex.com/p"),
    ("http://[::1]:8080/", "http://[::1]:8080/"),
    ("https://ex.com/?q=1", "https://ex.com/?q=1"),
])
def test_normalize_url_lowercases_drops_default_port_and_fragment(raw, clean):
    assert ssrf.normalize_url(raw) == clean


def test_normalize_url_is_idempotent():
    once = ssrf.normalize_url("HTTPS://Ex.com:8443/a;b?c=1#f")
    assert ssrf.normalize_url(once) == once


# -- domain_pattern / parse_domain_list --------------------------------------------

@pytest.mark.parametrize("raw, pattern", [
    (" Example.COM. ", "example.com"),
    ("*.example.com", "*.example.com"),
    ("https://Example.com/x", "example.com"),
    ("bücher.de", "xn--bcher-kva.de"),
    ("*.bücher.de", "*.xn--bcher-kva.de"),
    ("a" * 63 + ".com", "a" * 63 + ".com"),
])
def test_domain_pattern_normalizes_hosts_and_wildcards(raw, pattern):
    assert ssrf.domain_pattern(raw) == pattern


@pytest.mark.parametrize("raw", [
    "", None, "*", "*.", "example.com:8080", "a" * 64 + ".com", "-a.com", "a-.com",
    "ex ample.com", "a/b", "user@example.com", "_dmarc.example.com",
])
def test_domain_pattern_returns_empty_for_non_hostnames(raw):
    assert ssrf.domain_pattern(raw) == ""


def test_domain_pattern_returns_empty_for_a_double_wildcard():
    assert ssrf.domain_pattern("*.*.example.com") == ""
    assert ssrf.parse_domain_list("*.*.example.com\n*.example.com") == ["*.example.com"]


def test_domain_pattern_is_idempotent():
    for raw in ("*.Bücher.de", "HTTPS://Example.com/", "a.b.c"):
        once = ssrf.domain_pattern(raw)
        assert ssrf.domain_pattern(once) == once


def test_parse_domain_list_splits_on_commas_and_lines_and_drops_comments():
    assert ssrf.parse_domain_list("a.com, b.com\n# all of c\nc.com # trailing\n\n") == ["a.com", "b.com", "c.com"]


def test_parse_domain_list_keeps_first_seen_order_and_drops_duplicates():
    # A set() "optimization" would lose the order the admin wrote.
    assert ssrf.parse_domain_list("z.com\na.com\nZ.com.\nm.com\na.com") == ["z.com", "a.com", "m.com"]


def test_parse_domain_list_skips_invalid_entries():
    assert ssrf.parse_domain_list("good.com\nnot a host\nexample.com:81\n*.ok.org") == ["good.com", "*.ok.org"]


def test_parse_domain_list_returns_empty_for_empty_input():
    assert ssrf.parse_domain_list("") == []
    assert ssrf.parse_domain_list(None) == []


def test_parse_domain_list_stops_at_the_limit():
    raw = "\n".join(f"h{i}.com" for i in range(10))
    assert ssrf.parse_domain_list(raw, limit=3) == ["h0.com", "h1.com", "h2.com"]


def test_parse_domain_list_limit_counts_unique_entries():
    assert ssrf.parse_domain_list("a.com\na.com\na.com\nb.com", limit=2) == ["a.com", "b.com"]


# -- host_matches / domain_allowed -------------------------------------------------

@pytest.mark.parametrize("host, pattern, expected", [
    ("a.example.com", "*.example.com", True),
    ("x.a.example.com", "*.example.com", True),
    ("A.Example.COM.", "*.example.com", True),
    ("example.com", "*.example.com", False),
    ("badexample.com", "*.example.com", False),
    ("example.com", "EXAMPLE.com", True),
    ("a.example.com", "example.com", False),
    ("example.com.evil.org", "example.com", False),
    ("", "example.com", False),
    ("example.com", "", False),
])
def test_host_matches(host, pattern, expected):
    assert ssrf.host_matches(host, pattern) is expected


def test_domain_allowed_with_empty_lists_allows_everything():
    assert ssrf.domain_allowed("example.com", blacklist=[], whitelist=[])


def test_domain_allowed_blacklist_wins_over_whitelist():
    assert not ssrf.domain_allowed("a.com", blacklist=["a.com"], whitelist=["a.com"])


def test_domain_allowed_whitelist_blocks_unlisted_hosts():
    assert not ssrf.domain_allowed("b.com", blacklist=[], whitelist=["a.com"])
    assert ssrf.domain_allowed("x.a.com", blacklist=[], whitelist=["*.a.com"])


def test_domain_allowed_matches_unicode_host_against_punycode_entry():
    assert not ssrf.domain_allowed("Bücher.de.", blacklist=["xn--bcher-kva.de"], whitelist=[])


# -- public_ips -------------------------------------------------------------------

def test_public_ips_returns_unique_addresses_in_resolver_order(dns):
    dns.add("example.com", "93.184.216.34", "2606:2800:220:1::", "93.184.216.34")
    assert ssrf.public_ips("example.com") == ["93.184.216.34", "2606:2800:220:1::"]


def test_public_ips_returns_empty_if_any_address_is_private(dns):
    # Dropping only the private answer would let a rebinding name through.
    dns.add("rebind.example", "93.184.216.34", "127.0.0.1")
    assert ssrf.public_ips("rebind.example") == []


def test_public_ips_returns_empty_for_mapped_private_v6(dns):
    dns.add("mapped.example", "::ffff:10.0.0.1")
    assert ssrf.public_ips("mapped.example") == []


def test_public_ips_never_resolves_a_blocked_name(dns):
    assert ssrf.public_ips("localhost") == []
    assert ssrf.public_ips("") == []
    assert dns.lookups == []


def test_public_ips_returns_empty_when_the_name_does_not_resolve(dns):
    assert ssrf.public_ips("nx.example") == []


def test_public_ips_returns_empty_on_resolver_unicode_error(monkeypatch):
    def boom(*_a, **_k):
        raise UnicodeError("label too long")
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", boom)
    assert ssrf.public_ips("a" * 64 + ".com") == []


def test_public_ips_returns_empty_for_an_unparseable_address(monkeypatch):
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", lambda host, port, **_k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", port)),
    ])
    assert ssrf.public_ips("odd.example") == []


# -- pin_https_url / wiki_page_url -------------------------------------------------

def test_pin_https_url_brackets_v6_and_keeps_path_and_query_without_fragment():
    assert ssrf.pin_https_url("https://ex.com/p;x?q=1#f", "2606:4700::1") == "https://[2606:4700::1]:443/p;x?q=1"


def test_pin_https_url_keeps_an_explicit_port():
    assert ssrf.pin_https_url("https://ex.com:8443/", "93.184.216.34") == "https://93.184.216.34:8443/"


@pytest.mark.parametrize("title, url", [
    ("C++ (lang)", "https://en.wikipedia.org/wiki/C%2B%2B_(lang)"),
    ("AC/DC", "https://en.wikipedia.org/wiki/AC%2FDC"),
    ("What?", "https://en.wikipedia.org/wiki/What%3F"),
    ("Ünïcode", "https://en.wikipedia.org/wiki/%C3%9Cn%C3%AFcode"),
    ("", "https://en.wikipedia.org/wiki/"),
])
def test_wiki_page_url_encodes_titles_into_one_path_segment(title, url):
    assert ssrf.wiki_page_url(title) == url
