"""The scan: env files only, parsed as KEY=value, and a candidate only when
the key or the value's own format says so. What the removed feature took for
secrets (code, paths, identifiers) must never be one."""
import pytest

from app import secret_scan

from ._values import GITHUB, HEX, JWT, OPENAI, PASSWORD, PEM, STRIPE


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


@pytest.mark.parametrize("line", [
    "PORT=3000", "DEBUG=true", "NODE_ENV=production", "API_URL=https://api.example.com/v1",
    "TOKEN_TTL=3600", "SECRET_KEY_FILE=/run/secrets/key", "PASSWORD_MIN_LENGTH=12", "AUTH_HEADER_NAME=X-Auth-Token",
    "DB_PASSWORD=postgres", "DB_PASSWORD=changeme", "API_KEY=your-api-key-here", "API_KEY=${OTHER_KEY}",
    "SECRET=$(cat /run/secret)", "JWT_SECRET=secret", "APP_SECRET=settings.app_secret", "API_KEY=process.env.API_KEY",
    "AUTH_TOKEN=test_token_value", "DB_PASSWORD=sunshine", "NEXT_PUBLIC_API_KEY=abcd1234efgh5678ijkl",
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
