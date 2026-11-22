from __future__ import annotations

import html
import ipaddress
import re
import socket
from urllib.parse import parse_qs, quote, urlparse, urlunparse

import idna

_BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata",
    "ip6-localhost",
    "ip6-loopback",
}


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def is_blocked_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS:
        return True
    if host.endswith(".internal") or host.endswith(".local") or host.endswith(".localhost"):
        return True
    if host.startswith("169.254."):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # The resolver reads inet_aton forms (127.1, 2130706433, 0x7f.1) as
        # the address they spell.
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return False
        except ValueError:  # an embedded NUL: no real host has one
            return True
    return not _is_global(ip)


_V4_COMPATIBLE = ipaddress.ip_network("::/96")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


def ascii_host(host: str) -> str:
    """The host in its ASCII (UTS-46 IDNA) form, '' if it has none."""
    host = (host or "").strip().lower().rstrip(".")
    if host.isascii():
        return host
    try:
        return idna.encode(host, uts46=True).decode("ascii").lower().rstrip(".")
    except (idna.IDNAError, UnicodeError):
        return ""


def _is_global(ip) -> bool:
    if ip.version == 4:
        return ip.is_global
    # Python 3.12 calls ::ffff:100.64.0.1 global though 100.64.0.1 is not.
    if ip.ipv4_mapped:
        return ip.ipv4_mapped.is_global
    if ip.is_site_local or ip in _NAT64_LOCAL:
        return False
    # IPv4-compatible, NAT64, 6to4 and Teredo addresses reach the IPv4 hosts
    # they embed (64:ff9b::7f00:1 is 127.0.0.1 through a NAT64 gateway).
    embedded = [ip.sixtofour, *(ip.teredo or ())]
    if ip in _V4_COMPATIBLE or ip in _NAT64:
        embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return ip.is_global and all(v4.is_global for v4 in embedded if v4 is not None)


def _netloc(host: str, port: int | None, scheme: str) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    if port in (None, default):
        return host
    return f"{host}:{port}"


def public_http_url(raw: str, *, https_only: bool = False) -> str:
    url = str(raw or "").strip()
    if not url or len(url) > 2000:
        return ""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except Exception:
        return ""
    if parsed.scheme not in (("https",) if https_only else ("http", "https")):
        return ""
    if parsed.username or parsed.password:
        return ""
    host = ascii_host(parsed.hostname or "")
    if is_blocked_host(host):
        return ""
    if port not in (None, 80, 443):
        return ""
    path = parsed.path or "/"
    if parsed.params:
        path += ";" + parsed.params
    query = parsed.query or ""
    netloc = _netloc(host, port, parsed.scheme)
    return urlunparse((parsed.scheme, netloc, path, "", query, ""))


def decode_ddg_url(href: str) -> str:
    href = html.unescape(str(href or "").strip())
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
    except Exception:
        return ""
    host = (parsed.hostname or "").lower()
    if (host == "duckduckgo.com" or host.endswith(".duckduckgo.com")) and parsed.path.startswith("/l"):
        qs = parse_qs(parsed.query)
        target = (qs.get("uddg") or [""])[0]
        if target:
            href = target  # parse_qs already decoded it; a second pass alters %2F etc.
    return public_http_url(href)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    scheme = (parsed.scheme or "https").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    path = parsed.path or "/"
    if parsed.params:
        path += ";" + parsed.params
    query = parsed.query or ""
    return f"{scheme}://{_netloc(host, port, scheme)}{path}" + (f"?{query}" if query else "")


def allowed_fetch_url(url: str, allowed: set[str] | None = None) -> str:
    """Public HTTPS whose host passes the site domain policy. `allowed` is unused."""
    clean = public_http_url(url, https_only=True)
    if not clean:
        return ""
    host = host_of(clean)
    if not host or not domain_allowed(host):
        return ""
    return clean


_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def domain_pattern(raw: str) -> str:
    """Normalize one blacklist/whitelist entry. '' if it is not a hostname or *.suffix."""
    text = (raw or "").strip().lower().rstrip(".")
    if text.startswith("https://") or text.startswith("http://"):
        text = host_of(text)
    if "/" in text or "@" in text or ":" in text or " " in text:
        return ""
    if text.startswith("*."):
        rest = ascii_host(text[2:])
        if not rest or not _DOMAIN_RE.match(rest):
            return ""
        return "*." + rest
    text = ascii_host(text)
    if not text or not _DOMAIN_RE.match(text):
        return ""
    return text


def parse_domain_list(raw: str, *, limit: int = 500) -> list[str]:
    out: list[str] = []
    for line in str(raw or "").replace(",", "\n").splitlines():
        item = domain_pattern(line.split("#", 1)[0])
        if item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def host_matches(host: str, pattern: str) -> bool:
    host = (host or "").lower().rstrip(".")
    pattern = (pattern or "").lower()
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != suffix[1:]
    return host == pattern


def domain_allowed(host: str, *, blacklist: list[str] | None = None, whitelist: list[str] | None = None) -> bool:
    """Blacklist always blocks. A non-empty whitelist blocks everything else."""
    if blacklist is None or whitelist is None:
        from .policy import domain_lists

        stored_black, stored_white = domain_lists()
        if blacklist is None:
            blacklist = stored_black
        if whitelist is None:
            whitelist = stored_white
    host = ascii_host(host)
    if any(host_matches(host, item) for item in blacklist):
        return False
    if whitelist and not any(host_matches(host, item) for item in whitelist):
        return False
    return True


def public_ips(host: str, port: int = 443) -> list[str]:
    host = (host or "").lower().rstrip(".")
    if not host or is_blocked_host(host):
        return []
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):  # UnicodeError: a label longer than 63 chars
        return []
    addrs: list[str] = []
    for info in infos:
        ip_s = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_s)
        except ValueError:
            return []
        if not _is_global(ip):
            return []
        if ip_s not in addrs:
            addrs.append(ip_s)
    return addrs


def ips_public(host: str) -> bool:
    return bool(public_ips(host))


def pin_https_url(url: str, ip: str) -> str:
    parsed = urlparse(url)
    port = parsed.port or 443
    if ":" in ip:
        netloc = f"[{ip}]:{port}"
    else:
        netloc = f"{ip}:{port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, ""))


def wiki_page_url(title: str) -> str:
    slug = quote((title or "").replace(" ", "_"), safe="()_,-'")
    return public_http_url(f"https://en.wikipedia.org/wiki/{slug}") or ""
