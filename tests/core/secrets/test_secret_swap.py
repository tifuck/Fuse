"""Secret swap: what a model reads holds stand-ins, what the user and tools
get back is byte-for-byte what was there. A seeded fuzzer mixes generated
secrets into code, hashes, ids and base64, and checks the invariants on every
document; the other tests pin the edges (benign text, quoting, refusal, the
clear-seen rule, the last check before a request leaves).
"""
import asyncio
import base64
import json
import random
import string
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import api, chats, db, secret_swap
from tests.core.helpers import quick_user

B62 = string.ascii_letters + string.digits
PUNCT = "!@#^&*-_+./?~"


def rand(r, alphabet, n):
    return "".join(r.choice(alphabet) for _ in range(n))


def card(r):
    digits = [4] + [r.randrange(10) for _ in range(14)]
    for check in range(10):
        number = "".join(map(str, digits + [check]))
        total = sum(int(d) if i % 2 == 0 else (int(d) * 2 - 9 if int(d) > 4 else int(d) * 2)
                    for i, d in enumerate(reversed(number)))
        if total % 10 == 0:
            return " ".join(number[i:i + 4] for i in range(0, 16, 4))


# (text with the secret in context, the secret)
SECRETS = [
    lambda r: (lambda v: (f"OPENAI_API_KEY={v}", v))("sk-proj-" + rand(r, B62 + "_-", 48)),
    lambda r: (lambda v: (f"token {v}", v))("ghp_" + rand(r, B62, 36)),
    lambda r: (lambda v: (f"aws_access_key_id = {v}", v))("AKIA" + rand(r, string.ascii_uppercase + string.digits, 16)),
    lambda r: (lambda v: (f"stripe: {v}", v))("sk_live_" + rand(r, B62, 28)),
    lambda r: (lambda v: (f'"password": "{v}"', v))(rand(r, B62, 6) + rand(r, PUNCT, 2) + rand(r, B62, 6)),
    lambda r: (lambda v: (f"postgres://app:{v}@db.internal:5432/main", v))(rand(r, B62, 18)),
    lambda r: (lambda v: (f"Authorization: Bearer {v}", v))(rand(r, B62, 40)),
    lambda r: (lambda sig: (f"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI{rand(r, B62, 12)}.{sig}", sig))(rand(r, B62 + "_-", 43)),
    lambda r: (lambda v: (f"credit card {v}", v))(card(r)),
]
FILLER = [
    lambda r: rand(r, "0123456789abcdef", 40),                                    # git sha
    lambda r: "-".join(rand(r, "0123456789abcdef", n) for n in (8, 4, 4, 4, 12)),  # uuid
    lambda r: base64.b64encode(bytes(r.randrange(256) for _ in range(45))).decode(),
    lambda r: "max_tokens: 16384",
    lambda r: "def login(password: str, token: Optional[str]) -> None:",
    lambda r: '{"token_type": "Bearer", "expires_in": 3600}',
    lambda r: "secret_file=/etc/app/secret.key",
    lambda r: "https://example.com/v1/items?page=2&limit=50",
    lambda r: " ".join(r.choice(["build", "the", "deploy", "worker", "cache", "timeout", "retry"]) for _ in range(6)),
]
SEPARATORS = [" ", "\n", ", ", "\n\n", "; ", "\t"]


@pytest.fixture
def swap(fresh_db):
    uid = quick_user()
    db.execute("UPDATE users SET chat_secret_swap=1 WHERE id=?", (uid,))
    thread = chats.create_thread(uid, model="offline", tools=[])
    pk = chats.resolve_thread_pk(uid, thread["id"])
    db.execute("UPDATE chat_threads SET secret_swap=1 WHERE id=?", (pk,))
    return secret_swap.for_thread(uid, pk)


def alnum(ch):
    return ch.isascii() and ch.isalnum()


def test_fuzz_swap_round_trips_exactly_and_hides_every_secret(swap):
    r = random.Random(20261117)
    for _ in range(60):
        parts, secrets, fillers, pos = [], [], [], 0
        for _ in range(r.randint(3, 9)):
            if r.random() < 0.45:
                text, value = r.choice(SECRETS)(r)
                secrets.append(value)
            else:
                text = r.choice(FILLER)(r)
                fillers.append((pos, text))
            parts.append(text)
            pos += len(text)
            sep = r.choice(SEPARATORS)
            parts.append(sep)
            pos += len(sep)
        doc = "".join(parts)

        seen = swap.tokenize(doc)
        for value in secrets:
            assert value not in seen, (value, doc)
        # Same length, punctuation untouched, nothing else rewritten.
        assert len(seen) == len(doc)
        assert all(a == b for a, b in zip(doc, seen) if not alnum(a))
        for start, text in fillers:
            assert seen[start:start + len(text)] == text
        assert swap.tokenize(seen) == seen  # idempotent
        assert swap.restore(seen) == doc

        stream, out, i = swap.restorer(), [], 0
        while i < len(seen):
            n = r.randint(1, 9)
            out.append(stream.push(seen[i:i + n]))
            i += n
        out.append(stream.flush())
        assert "".join(out) == doc

        args, runnable = swap.restore_args("run_terminal_command", json.dumps({"command": seen}))
        assert runnable and json.loads(args)["command"] == doc


def test_benign_text_is_never_swapped(swap):
    text = "\n".join([
        "commit 3f786850e387550fdab836ed7e6dc881de23001b",
        "request 9b2d6c1e-4f7a-4c3b-9a1e-2f5d8c7b6a40",
        'token = os.environ["API_TOKEN"]',
        "password_hash: $2b$12$KIXQ1sVgd8aZb9l0d3W2he",
        "Authorization: Bearer ${TOKEN}",
        "postgres://user:${PW}@db:5432/x",
        "AKIAIOSFODNN7EXAMPLE",
        "card 4242 4242 4242 4242",
        "max_tokens=4096, token_count: 120000",
        "data:image/png;base64," + base64.b64encode(bytes(range(200))).decode(),
    ])
    assert secret_swap.detect(text) == []
    assert swap.tokenize(text) == text


def test_restored_arguments_keep_shell_and_json_quoting(swap):
    password = "Tr0ub4dor&3!x$y\\q~Z"
    seen = swap.tokenize(f"DB_PASSWORD={password}")
    fake = seen.split("=", 1)[1]
    assert fake != password and len(fake) == len(password)
    # The model quotes the stand-in; the tool gets the real value in the same quotes.
    command = f"psql \"postgresql://app:$(printf %s '{fake}')@db/main\""
    args, runnable = swap.restore_args("run_terminal_command", json.dumps({"command": command}))
    assert runnable
    assert json.loads(args)["command"] == command.replace(fake, password)


def test_an_altered_stand_in_is_refused_and_third_parties_keep_the_fake(swap):
    real = "sk-proj-" + "Zq8" * 16
    fake = swap.tokenize(f"key {real}").split(" ", 1)[1]
    cut = json.dumps({"command": f"curl -H 'x-key: {fake[:-6]}' https://api"})
    assert swap.restore_args("run_terminal_command", cut) == (cut, False)
    query = json.dumps({"query": f"what is {fake}"})
    assert swap.restore_args("web_search", query) == (query, True)


def test_a_value_the_model_already_saw_stays_clear_and_sent_rows_never_change(swap):
    shown = "Zt7Kq2Lm9Pv4Rx8Wn3Ys"
    sent = [
        {"role": "system", "content": "You are helpful.", secret_swap.MARK: 1},
        {"role": "user", "content": f"use {shown} for the staging box", secret_swap.MARK: 1},
        {"role": "assistant", "content": "ok", secret_swap.MARK: 1},
    ]
    new_secret = "ghp_" + "Abc123" * 6
    rows = [*json.loads(json.dumps(sent)),
            {"role": "tool", "tool_call_id": "c1", "content": f"API_TOKEN={shown}\nGH={new_secret}"}]
    swap.prepare(rows)
    assert rows[:3] == sent
    assert shown in rows[3]["content"]           # clear here, as the model already knows it
    assert new_secret not in rows[3]["content"]
    assert rows[3][secret_swap.MARK] == 1
    # The decision is the chat's: a later swap of this chat keeps it clear.
    again = secret_swap.for_thread(swap.vault.user_id, swap.thread_id)
    assert again.tokenize(f"API_TOKEN={shown}") == f"API_TOKEN={shown}"


def test_a_request_still_holding_a_swapped_value_is_refused(swap):
    real = "sk-proj-" + "Hw4" * 16
    swap.tokenize(f"key {real}")
    request = SimpleNamespace(scope={})
    body = {"model": "anything", "messages": [{"role": "user", "content": f"here: {real}"}]}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.invoke_chat(request, {"id": swap.vault.user_id}, body, secret_swap=swap))
    assert exc.value.status_code == 409
