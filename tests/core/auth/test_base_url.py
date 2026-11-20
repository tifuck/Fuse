"""allowed_base_url: admin site sources may point at private hosts; any other
caller gets https to a host whose every address is public (DNS faked)."""
import socket

from app.security import allowed_base_url

ADDRS = {
    "api.example.com": ["93.184.216.34"],
    "lan.example.com": ["10.0.0.206"],
    "mixed.example.com": ["93.184.216.34", "127.0.0.1"],
    "v6.example.com": ["::ffff:192.168.1.5"],
}


def _fake_dns(monkeypatch):
    def getaddrinfo(host, port, *args, **kwargs):
        if host not in ADDRS:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ADDRS[host]]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def test_user_facing_urls_need_https_and_public_addresses(monkeypatch):
    _fake_dns(monkeypatch)
    assert allowed_base_url("https://api.example.com/v1")
    for url in (
        "http://api.example.com/v1",  # not https
        "https://lan.example.com/v1",  # resolves private
        "https://mixed.example.com/v1",  # one private address is enough
        "https://v6.example.com/v1",  # private behind an IPv4-mapped address
        "https://nowhere.example.com/v1",  # does not resolve
        "https://user:pw@api.example.com/v1",  # credentials in the URL
        "https://127.0.0.1/v1", "https://localhost/v1", "https://169.254.169.254/latest",
        "https://metadata.google.internal/", "https://api.example.com:99999/v1", "ftp://api.example.com/", "",
    ):
        assert not allowed_base_url(url), url


def test_admin_site_sources_may_use_private_hosts_but_never_metadata(monkeypatch):
    _fake_dns(monkeypatch)
    for url in ("http://10.0.0.206:8080/v1", "http://lan.example.com/v1", "https://api.example.com/v1"):
        assert allowed_base_url(url, allow_private=True), url
    for url in ("http://169.254.169.254/", "http://metadata/", "http://x.internal/", "file:///etc/passwd"):
        assert not allowed_base_url(url, allow_private=True), url
