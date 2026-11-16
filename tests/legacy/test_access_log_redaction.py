"""uvicorn's access log must not record one-time secrets carried in URLs."""
import logging

from app.security import AccessLogRedactor, redact_url


def test_secret_urls_are_redacted():
    cases = {
        "/i/fuse_AbC123": "/i/[redacted]",
        "/i/fuse_AbC123.ps1": "/i/[redacted]",
        "/register?invite=tok123": "/register?invite=[redacted]",
        "/register?setup=tok123&x=1": "/register?setup=[redacted]&x=1",
        "/sources/nanogpt/callback?code=c0de&state=st4te": "/sources/nanogpt/callback?code=[redacted]&state=[redacted]",
        "/pool/mimo/callback?u=blob&state=s": "/pool/mimo/callback?u=[redacted]&state=[redacted]",
        "/x?token=t": "/x?token=[redacted]",
        # Lookalike names and ordinary paths pass through.
        "/chat/threads?cursor=5&unicode=1": "/chat/threads?cursor=5&unicode=1",
        "/install.sh": "/install.sh",
        "/icons/i/x.svg": "/icons/i/x.svg",
    }
    for raw, logged in cases.items():
        assert redact_url(raw) == logged, raw


def test_filter_rewrites_the_access_record_and_app_installs_it():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", "/register?invite=secret", "1.1", 200), None,
    )
    assert AccessLogRedactor().filter(record)
    assert "secret" not in record.getMessage()
    assert "/register?invite=[redacted]" in record.getMessage()

    import app.main  # noqa: F401

    assert any(isinstance(f, AccessLogRedactor) for f in logging.getLogger("uvicorn.access").filters)
