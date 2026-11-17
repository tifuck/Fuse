"""app.main's request edge: one-time secrets never reach the access log,
oversized bodies are refused before any handler, and every page carries the
security headers."""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.security import AccessLogRedactor, redact_url


@pytest.mark.parametrize("raw, logged", [
    ("/i/fuse_AbC123", "/i/[redacted]"),
    ("/i/fuse_AbC123.ps1?x=1", "/i/[redacted]?x=1"),
    ("/register?invite=tok123", "/register?invite=[redacted]"),
    ("/register?setup=tok123&x=1", "/register?setup=[redacted]&x=1"),
    ("/sources/nanogpt/callback?code=c0de&state=st4te", "/sources/nanogpt/callback?code=[redacted]&state=[redacted]"),
    ("/pool/mimo/callback?u=blob&state=s", "/pool/mimo/callback?u=[redacted]&state=[redacted]"),
    ("/x?TOKEN=t", "/x?TOKEN=[redacted]"),
    # Lookalike names and ordinary paths pass through.
    ("/chat/threads?cursor=5&unicode=1", "/chat/threads?cursor=5&unicode=1"),
    ("/install.sh", "/install.sh"),
    ("/icons/i/x.svg", "/icons/i/x.svg"),
])
def test_secret_urls_are_redacted(raw, logged):
    assert redact_url(raw) == logged


def test_the_access_log_filter_rewrites_the_record_and_app_main_installs_it():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:5", "GET", "/register?invite=secret", "1.1", 200), None)
    assert AccessLogRedactor().filter(record)
    assert "secret" not in record.getMessage() and "/register?invite=[redacted]" in record.getMessage()

    import app.main  # noqa: F401

    assert any(isinstance(f, AccessLogRedactor) for f in logging.getLogger("uvicorn.access").filters)


@pytest.fixture
def edge(fresh_db):
    """app.main itself, middleware included; without `with` no lifespan runs."""
    from app import main

    trusted = [h.strip() for h in settings.trusted_hosts.split(",") if h.strip()]
    return TestClient(main.app, base_url=f"https://{trusted[0] if trusted else 'testserver'}")


def test_pages_carry_the_security_headers(edge):
    res = edge.get("/login")
    assert res.status_code == 200
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["strict-transport-security"].startswith("max-age=")


@pytest.mark.parametrize("cookie, zone", [
    ("fuse_tz=America/New_York; other=1", "America/New_York"),
    ("fuse_tz=../etc/passwd", "UTC"),
    ("", "UTC"),
])
def test_the_fuse_tz_cookie_sets_the_display_zone(cookie, zone):
    from app import main, stats

    seen = []

    async def inner(scope, receive, send):
        seen.append(str(stats.display_zone.get()))

    headers = [(b"cookie", cookie.encode())] if cookie else []
    asyncio.run(main.DisplayZoneMiddleware(inner)({"type": "http", "headers": headers}, None, None))
    assert seen == [zone]


def test_oversized_or_malformed_bodies_are_refused_before_any_handler(edge):
    big = edge.post("/login", content=b"x" * (settings.max_body_bytes + 1),
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert big.status_code == 413
    bad = edge.post("/login", content=b"x=1", headers={"content-length": "-1"})
    assert bad.status_code == 400
