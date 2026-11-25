"""The scan: env files only, parsed as KEY=value, and a candidate only when
the key or the value's own format says so. What the removed feature took for
secrets (code, paths, identifiers) must never be one."""
import asyncio

import pytest

from app import secret_guard, secret_scan, workspace
from tests.core.helpers import quick_user, run_async, wait_until_async

from ._values import ANTHROPIC, CUSTOM, GITHUB, HEX, JWT, OPENAI, PASSWORD, PEM, SLASHY, STRIPE, WORDS


@pytest.mark.parametrize("name,ok", [
    (".env", True), (".env.local", True), ("apps/web/.env.production", True), ("config.env", True),
    (".envrc", True), (".npmrc", True), (".pypirc", True),
    (".env.example", False), (".env.sample", False), ("prod.env.template", False), ("environment.py", False),
    ("env.md", False), ("README.md", False),
])
def test_only_env_files_are_read(name, ok):
    assert secret_scan.is_env_name(name) is ok


def test_vendor_folders_are_skipped_and_the_nearest_files_come_first():
    names = ["node_modules/x/.env", "src/", "a/b/.env", ".env", ".git/.env", "pkg/.env.local", "main.py"]
    assert secret_scan.pick_files(names) == [".env", "pkg/.env.local", "a/b/.env"]


def test_the_dotenv_grammar():
    text = "\r\n".join([
        "# comment", "", "export A_TOKEN=abc123 # trailing", "B='single # kept'", 'C="line1\\nline2"',
        'D="first', 'second"', "EMPTY=", "bare line", "[section]", "password = spaced",
    ])
    assert secret_scan.parse(text) == [
        (1, "", "comment"), (3, "A_TOKEN", "abc123"), (4, "B", "single # kept"), (5, "C", "line1\nline2"), (6, "D", "first\nsecond"),
        (9, "", "bare line"), (11, "password", "spaced"),
    ]


def found(text):
    return [(f.line, f.key, f.value, f.kind) for f in secret_scan.find(".env", text)]


def test_what_is_offered():
    text = "\n".join([
        f"OPENAI_API_KEY={OPENAI}", f"anything={STRIPE}", f"DB_PASSWORD={PASSWORD}",
        f"DATABASE_URL=postgres://app:{PASSWORD}@db.internal:5432/app", f"SESSION_SECRET={HEX}", f"SUPABASE_JWT={JWT}",
        f'SIGNING_KEY="{PEM}"', f"junk before {GITHUB} and after", f"# OLD_STRIPE={STRIPE[:-4]}Zz9Q",
    ])
    got = found(text)
    assert (1, "OPENAI_API_KEY", OPENAI, "openai") in got
    assert (2, "anything", STRIPE, "stripe") in got
    assert (3, "DB_PASSWORD", PASSWORD, "named") in got
    assert (5, "SESSION_SECRET", HEX, "named") in got
    assert (6, "SUPABASE_JWT", JWT, "jwt") in got
    assert any(kind == "pem" and value == PEM for _l, _k, value, kind in got)
    # The same password inside the URL is the same value: listed once.
    assert [v for _l, _k, v, _kind in got].count(PASSWORD) == 1
    # A line with no key: only a provider-format value counts, and it has no name.
    keyless = [row for row in got if row[2] == GITHUB]
    assert keyless and keyless[0][1] == "" and keyless[0][3] == "github"
    # A commented-out assignment is still in the file: offered under its name.
    assert any(key == "OLD_STRIPE" and kind == "stripe" for _l, key, _v, kind in got)


def test_a_realistic_env_file():
    """What a project's .env really holds: every credential is found, under
    its name, and none of the settings beside them."""
    secrets = {
        "SECRET_KEY": HEX, "DATABASE_URL": f"postgres://shop:{PASSWORD}@db.internal:5432/shop",
        "REDIS_URL": "redis://:" + "Zx9Qm2Lk8Vb4Np6T" + "@cache:6379/0",   # no user name, as Redis URLs are written
        "AWS_SECRET_ACCESS_KEY": SLASHY, "METRICS_TOKEN": CUSTOM, "RATELIMIT_BYPASS_KEY": WORDS,
        "APP_KEY": "base64:" + "Qx7Lm2Vn9Rt4Wp8Zk3Jh6Fd1Sb5Yc0GaQx7Lm2Vn9Rs=", "MAILER_KEY": "key-" + "3ax6xnjp29jd6fds" * 2,
        "STRIPE_SECRET_KEY": STRIPE, "OPENAI_API_KEY": OPENAI, "GH_TOKEN": GITHUB,
        "ENCRYPTION_KEY": "5b0e84a0-e29b-41d4-" + "a716-4466f5440c1d", "SMTP_PASS": "Wint3r!sC0ming",
    }
    settings = [
        "APP_ENV=production", "APP_URL=https://shop.example.com", "PORT=8080", "LOG_LEVEL=info", "DB_HOST=db.internal",
        "DB_USER=shop", "AWS_REGION=eu-central-1", "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", "S3_BUCKET=shop-uploads",
        "SENTRY_DSN=https://" + "9f2c4e7a1b3d5f60" + "@o123.ingest.sentry.io/42", "GOOGLE_CLIENT_ID=1234567890-abcdefgh.apps.googleusercontent.com",
        "SESSION_TTL=86400", "PRIMARY_KEY=id", "SORT_KEY=created_at", "CACHE_KEY_PREFIX=shop:v2:", "SSH_KEY_FILE=/home/dev/.ssh/id_ed25519",
        "JWT_PUBLIC_KEY=" + "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE", "MONKEY=Banana12!x", "FEATURE_FLAGS=checkout_v2,search",
        "ADMIN_PASSWORD=hunter2", "MAIL_FROM=shop@example.com", "TZ=Europe/Berlin",
    ]
    lines = settings[:8] + [f"{name}={value}" for name, value in secrets.items()] + settings[8:]
    got = {f.key: f.value for f in secret_scan.find(".env", "\n".join(lines))}
    want = dict(secrets, DATABASE_URL=PASSWORD, REDIS_URL="Zx9Qm2Lk8Vb4Np6T")   # of a URL, only its password
    assert got == want


def scanned_from(monkeypatch, files, gate=None):
    """The host's env files are the dict the test edits (path -> text).
    Returns the list of reads: None for a listing, else the paths re-read."""
    reads = []

    async def read_env_files(user_id, host_id, root, *, pick, depth, max_chars, known=None):
        reads.append(known)
        if gate is not None:
            await gate.wait()
        return "box", [(name, files[name]) for name in (pick(list(files)) if known is None else known) if name in files]

    monkeypatch.setattr(workspace, "read_env_files", read_env_files)
    return reads


def test_a_folder_is_listed_once_then_only_its_env_files_are_read_again(fresh_db, monkeypatch):
    uid = quick_user()
    files = {".env": f"OPENAI_API_KEY={OPENAI}\n", "README.md": "x", "node_modules/pkg/.env": f"T={STRIPE}\n"}
    reads = scanned_from(monkeypatch, files)

    async def body():
        assert await secret_scan.ensure(uid, 5, "/srv/app") == 1
        # Another request right after: nothing ran on the machine, so nothing is read.
        assert await secret_scan.ensure(uid, 5, "/srv/app") == 0 and reads == [None]
        # A tool ran: the env files already found are read again, without listing the folder.
        files[".env"] += f"GH_TOKEN={GITHUB}\n"
        secret_scan.mark_dirty(uid, 5)
        assert await secret_scan.ensure(uid, 5, "/srv/app") == 1 and reads == [None, [".env"]]
        # A new chat lists the folder again, whenever the last listing was.
        files["api/.env.local"] = f"API_TOKEN={ANTHROPIC}\n"
        assert await secret_scan.ensure(uid, 5, "/srv/app", force=True) == 1 and reads[-1] is None
        assert await secret_scan.ensure(uid, 5, "/srv/app", force=True) == 0

    run_async(body())
    assert [(o["key"], o["file"]) for o in secret_guard.offers(uid)] == [
        ("OPENAI_API_KEY", ".env"), ("GH_TOKEN", ".env"), ("API_TOKEN", "api/.env.local")]


def test_a_slow_host_does_not_hold_the_turn_and_its_scan_still_lands(fresh_db, monkeypatch):
    uid = quick_user()

    async def body():
        gate = asyncio.Event()
        scanned_from(monkeypatch, {".env": f"OPENAI_API_KEY={OPENAI}\n"}, gate)
        await secret_scan.ensure_within(uid, 5, "/srv/app", timeout=0.01)
        assert not secret_guard.has_offers(uid)
        gate.set()   # the host answers at last
        await wait_until_async(lambda: secret_guard.has_offers(uid), message="the scan was dropped at its timeout")

    run_async(body())


def test_a_scan_whose_offer_failed_reads_the_folder_again(fresh_db, monkeypatch):
    uid = quick_user()
    reads = scanned_from(monkeypatch, {".env": f"OPENAI_API_KEY={OPENAI}\n"})
    real_offer = secret_guard.offer
    calls = []

    def offer(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        return real_offer(*args)

    monkeypatch.setattr(secret_guard, "offer", offer)

    async def body():
        assert await secret_scan.ensure(uid, 5, "/srv/app") == 0
        # The next round reads the folder again instead of waiting for a tool to run.
        assert await secret_scan.ensure(uid, 5, "/srv/app") == 1 and reads == [None, None]

    run_async(body())
    assert [o["value"] for o in secret_guard.offers(uid)] == [OPENAI]


def test_a_scan_whose_read_failed_reads_the_folder_again(fresh_db, monkeypatch):
    uid = quick_user()
    files = {".env": f"OPENAI_API_KEY={OPENAI}\n"}
    reads = scanned_from(monkeypatch, files)
    real_read = workspace.read_env_files

    async def read_env_files(*args, **kwargs):
        if len(reads) == 0:
            reads.append("failed")
            raise ConnectionError("host unreachable")
        return await real_read(*args, **kwargs)

    monkeypatch.setattr(workspace, "read_env_files", read_env_files)

    async def body():
        assert await secret_scan.ensure(uid, 5, "/srv/app") == 0
        # The next round lists the folder again instead of waiting out the rescan interval.
        assert await secret_scan.ensure(uid, 5, "/srv/app") == 1 and reads == ["failed", None]

    run_async(body())


def test_nothing_is_scanned_while_detection_is_off(fresh_db, monkeypatch):
    uid = quick_user()
    reads = scanned_from(monkeypatch, {".env": f"OPENAI_API_KEY={OPENAI}\n"})
    secret_guard.set_switches(uid, offer_on=False)
    assert run_async(secret_scan.ensure(uid, 5, "/srv/app", force=True)) == 0 and reads == []


@pytest.mark.parametrize("line", [
    "PORT=3000", "DEBUG=true", "NODE_ENV=production", "API_URL=https://api.example.com/v1",
    "TOKEN_TTL=3600", "SECRET_KEY_FILE=/run/secrets/key", "PASSWORD_MIN_LENGTH=12", "AUTH_HEADER_NAME=X-Auth-Token",
    "DB_PASSWORD=postgres", "DB_PASSWORD=changeme", "API_KEY=your-api-key-here", "API_KEY=${OTHER_KEY}",
    "SECRET=$(cat /run/secret)", "JWT_SECRET=secret", "APP_SECRET=settings.app_secret", "API_KEY=process.env.API_KEY",
    "AUTH_TOKEN=test_token_value", "DB_PASSWORD=sunshine", "NEXT_PUBLIC_API_KEY=" + "abcd1234" + "efgh5678ijkl",
    "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", "db_password:", "some_table_name_here", "@pytest.fixture",
    "app/chat_generation/tool_runner.py:9:from ..y import Z", "SESSION_SECRET=xxxxxxxxxxxxxxxx", "API_KEY=<your key>",
    "ADMIN_EMAIL=admin@example.com", "REDIS_PASSWORD=", "SECRET_KEY=1234",
])
def test_what_is_never_offered(line):
    assert found(line) == []


def test_the_old_false_positive_cannot_come_back():
    # The removed detector read a JSON-escaped newline as part of a value.
    text = 'db_password:\\nsome_table_name_here\nexcept secret_swap.SwapUnavailable:\\napp/x.py:9:from ..y import Z'
    assert found(text) == []
