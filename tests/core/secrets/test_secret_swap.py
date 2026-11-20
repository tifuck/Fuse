"""Secret swap: what a model reads holds stand-ins, what the user and tools
get back is byte-for-byte what was there. A seeded fuzzer mixes generated
secrets into code, hashes, ids, base64 and the benign corpus, and checks the
invariants on every document; a second fuzzer feeds garbage (control
characters, lone surrogates, odd row shapes) and requires no exception. The
corpora pin what must never and what must always be swapped; the other tests
pin the edges (quoting, refusal, clear-seen, scoping, the last check before a
request leaves).
"""
import asyncio
import base64
import json
import pathlib
import random
import re
import string
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import api, chats, db, secret_swap
from tests.core.helpers import quick_user

HERE = pathlib.Path(__file__).parent


def corpus(name):
    lines = (HERE / name).read_text().splitlines()
    # Key-shaped literals that GitHub push protection rejects are spelled
    # with a placeholder for their prefix.
    return [line.replace("\\n", "\n").replace("{sk_live}", "sk_" + "live_")
            for line in lines if line and not line.startswith("#")]


BENIGN = corpus("benign.txt")
MUST = [(line.replace("[[", "").replace("]]", ""), re.search(r"\[\[(.+?)\]\]", line).group(1))
        for line in corpus("must_swap.txt")]

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
    lambda r: r.choice(BENIGN),
]
SEPARATORS = [" ", "\n", ", ", "\n\n", "; ", "\t"]


def swapping_thread(uid):
    thread = chats.create_thread(uid, model="offline", tools=[])
    pk = chats.resolve_thread_pk(uid, thread["id"])
    db.execute("UPDATE chat_threads SET secret_swap=1 WHERE id=?", (pk,))
    return thread, pk


@pytest.fixture
def swap(fresh_db):
    uid = quick_user()
    db.execute("UPDATE users SET chat_secret_swap=1 WHERE id=?", (uid,))
    thread, pk = swapping_thread(uid)
    swap = secret_swap.for_thread(uid, pk)
    swap.public_id = thread["id"]
    return swap


def alnum(ch):
    return ch.isascii() and ch.isalnum()


def test_fuzz_swap_round_trips_exactly_and_hides_every_secret(swap):
    # About 4 s: each document stores new pairs (encrypted) in the database,
    # the same path a chat takes; fewer documents stop finding mixes.
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
    # Code references, placeholders, package names, hashes, ids, public
    # example keys, prose: none is a secret (tests/core/secrets/benign.txt).
    flagged = [(line, [line[h.start:h.end] for h in secret_swap.detect(line)])
               for line in BENIGN if secret_swap.detect(line)]
    assert flagged == []
    text = "\n".join(BENIGN)
    assert swap.tokenize(text) == text


def test_every_kind_of_secret_is_swapped_custom_keys_included(swap):
    missed = []
    for line, secret in MUST:
        seen = swap.tokenize(line)
        if secret in seen or len(seen) != len(line):
            missed.append(line)
        assert swap.restore(seen) == line
    assert missed == []


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
    real = "sk-proj-" + "Zq8Lw2Nc6Rv1" * 4
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
    # The transcript is the record: a later run on it (a new swap) agrees.
    again = secret_swap.for_thread(swap.vault.user_id, swap.thread_id)
    again.bind(lambda: rows)
    assert again.tokenize(f"API_TOKEN={shown}") == f"API_TOKEN={shown}"


def test_a_subagent_never_blocks_or_contradicts_the_main_agent(swap):
    # Production regression: the main agent saw a value unlabelled (nothing
    # detected); a subagent of the same chat then met it labelled. A shared
    # per-chat record marked it hidden, so the main agent's next request held
    # it "unswapped" and was refused on every retry.
    uid, value = swap.vault.user_id, "Zt7Kq2Lm9Pv4Rx8Wn3YsQe5Hd"
    main_rows = [{"role": "system", "content": "sys"},
                 {"role": "tool", "tool_call_id": "c1", "content": f"found {value} in the fixture"}]
    main = secret_swap.for_thread(uid, swap.thread_id)
    main.bind(lambda: main_rows)
    main.prepare(main_rows)
    child_rows = [{"role": "user", "content": "audit the tests"}]
    child = secret_swap.for_thread(uid, swap.thread_id)
    child.bind(lambda: child_rows)
    assert value not in child.tokenize(f"API_KEY={value}")   # the subagent never saw it: swapped
    # The main agent keeps the value it already knows, in new output too.
    main_next = secret_swap.for_thread(uid, swap.thread_id)
    main_next.bind(lambda: main_rows)
    main_rows.append({"role": "tool", "tool_call_id": "c2", "content": f"API_KEY={value}"})
    main_next.prepare(main_rows)
    assert main_rows[-1]["content"] == f"API_KEY={value}"
    assert main_next.leaks(main_rows) == []
    # And the last check before a request leaves never stops a chat: a value
    # the subagent swapped, still in a request, is logged and the request goes on.
    body = {"model": "no-such-model", "messages": [{"role": "user", "content": f"here: {value}"}]}
    assert child.leaks(body["messages"]) == ["assign"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.invoke_chat(SimpleNamespace(scope={}), {"id": uid}, body, secret_swap=child))
    assert exc.value.status_code == 404  # got past the check to model lookup


GARBAGE = (string.printable + "\x00\x01\x1b\x7f\u00e9\u00fc\u4e2d\u6587\u0645\u202e\u200b\ufeff"
           + "\U0001f600\U0001f512" + "\ud800\udfff" + "{}[]\"'\\`$|")


def test_garbage_never_raises_and_still_round_trips(swap):
    # Whatever a tool prints or a user pastes, a chat never errors on it.
    r = random.Random(7)
    for _ in range(80):
        pieces = [rand(r, GARBAGE, r.randint(0, 60)) for _ in range(r.randint(1, 6))]
        if r.random() < 0.5:
            pieces.insert(r.randrange(len(pieces) + 1), r.choice(SECRETS)(r)[0])
        if r.random() < 0.3:
            pieces.insert(0, "API_KEY=" + rand(r, GARBAGE, r.randint(8, 40)))
        text = "".join(pieces)
        seen = swap.tokenize(text)
        assert swap.restore(seen) == text
        stream, out = swap.restorer(), []
        for i in range(0, len(seen), 3):
            out.append(stream.push(seen[i:i + 3]))
        assert "".join(out) + stream.flush() == text
        args, _runnable = swap.restore_args("run_terminal_command", json.dumps({"command": seen}))
        json.loads(args)
        swap.bind(lambda: [{"role": "user", "content": seen}])
        assert swap.to_model(swap.restore(seen)) == seen
        swap.bind(None)


def test_odd_rows_never_raise_and_signed_or_binary_parts_are_untouched(swap):
    key = "ghp_" + "Kq7Lm2Pv9Rx4Tz8Wn3Yb6Hc1Gj5Fd0Sa2Qe8"
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(key.encode() * 4).decode()}}
    thinking = [{"thinking": f"the key is {key}", "signature": "c2lnbmF0dXJl"}]
    details = [{"type": "reasoning.encrypted", "data": f"opaque {key}"}]
    rows = [
        "not a row", None, {"role": "user", "content": None}, {"role": "user", "content": 42},
        {"role": "user", "content": [None, "bare", image, {"type": "text", "text": f"use {key}"}]},
        {"role": "assistant", "content": "ok", "tool_calls": {"not": "a list"}},
        {"role": "assistant", "content": "", "tool_calls": [None, {"function": None},
                                                            {"function": {"name": "x", "arguments": {"a": 1}}}]},
        {"role": "assistant", "content": "done", "claude_thinking": thinking, "reasoning_details": details},
        {"role": "tool", "tool_call_id": "c1", "content": f"out {key}"},
    ]
    swap.prepare(rows)
    assert rows[4]["content"][2] == image
    assert rows[7]["claude_thinking"] == thinking and rows[7]["reasoning_details"] == details
    assert key not in rows[4]["content"][3]["text"] and key not in rows[8]["content"]
    assert swap.leaks([r for r in rows if isinstance(r, dict)]) == []


def test_live_output_never_shows_part_of_a_stand_in(swap):
    from app.chat_output import Output

    key = "sk-proj-" + "Vb3Nq8Lx1Tz6Rw4Kp9Mc2Yh7" * 2
    fake = swap.tokenize(f"k {key}")[2:]
    output = Output({})
    output.swap = swap

    def delta(text, field="content"):
        return {"choices": [{"index": 0, "delta": {field: text}}]}

    # Split mid-stand-in by tiny deltas and by events that arrive mid-reply
    # (a thinking marker, a heartbeat, a meter); then the round's tool call.
    frames = [delta("Using "), delta(fake[:13]),
              {"fuse": {"event": "activity", "phase": "model", "thinking": True}},
              delta(fake[13:30]), {"fuse": {"event": "heartbeat"}}, {"fuse": {"event": "context", "used": 10}},
              delta(fake[30:]), delta(" now."),
              delta(fake[:20], "reasoning_content"), delta(fake[20:], "reasoning_content"),
              {"fuse": {"event": "tool", "id": "t1", "name": "run_terminal_command", "status": "running",
                        "command": f"deploy --key {fake}"}},
              {"fuse": {"event": "activity", "phase": "model"}}, delta("Done with "), delta(fake[:-3]), delta(fake[-3:])]
    shown = []
    for obj in frames:
        output.apply(output.restore_event(obj))
        shown.append(json.dumps(output.stored(light=True)))
    output.flush_swap()
    output.settle_tool_xml()
    stored = output.stored()
    shown.append(json.dumps(stored))
    # At no point, not even mid-stream, did any piece of the stand-in show.
    for snapshot in shown:
        for i in range(len(fake) - 11):
            assert fake[i:i + 12] not in snapshot
    assert stored["content"] == f"Using {key} now.\n\nDone with {key}"
    assert stored["reasoning"] == key
    assert stored["tools"][0]["command"] == f"deploy --key {key}"


def test_weak_values_are_scoped_to_their_transcript(swap):
    # A short password is swapped where detected, and again only where its
    # stand-in is already in view; elsewhere an equal string is left alone.
    rows = [{"role": "tool", "tool_call_id": "c1", "content": "DB_PASSWORD=Biscuit2019!"}]
    swap.bind(lambda: rows)
    swap.prepare(rows)
    assert "Biscuit2019!" not in rows[0]["content"]
    assert "Biscuit2019!" not in swap.tokenize("login with Biscuit2019! today")
    other = secret_swap.for_thread(swap.vault.user_id, swapping_thread(swap.vault.user_id)[1])
    other.bind(lambda: [{"role": "user", "content": "hello"}])
    assert other.tokenize("login with Biscuit2019! today") == "login with Biscuit2019! today"


def test_same_secret_from_parallel_runs_gets_one_stand_in(swap):
    from concurrent.futures import ThreadPoolExecutor

    key = "sk-proj-" + "Pr7Lk2Mz9Qw4Vn8Xc3Bh6Tj1" * 2
    secret_swap._vaults.clear()  # each run starts cold, as after a restart

    def one(_):
        fresh = secret_swap.for_thread(swap.vault.user_id, swap.thread_id)
        return fresh.tokenize(f"KEY={key}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        seen = set(pool.map(one, range(6)))
    assert len(seen) == 1 and key not in seen.pop()
    row = db.fetchone("SELECT COUNT(*) AS n FROM secret_tokens WHERE user_id=?", (swap.vault.user_id,))
    assert row["n"] == 1


def test_switch_and_fork_rules(swap):
    uid = swap.vault.user_id
    key = "ghp_" + "Fk8Wq3Lz6Np1Tx9Rv4Mc7Yb2Hs5Gd0Je3Ka"
    seen = swap.tokenize(f"T={key}")
    # Turning the account switch off never stops a chat that already swaps.
    chats.set_user_context(uid, secret_swap=False)
    assert secret_swap.for_thread(uid, swap.thread_id) is not None
    fresh = chats.create_thread(uid, model="offline", tools=[])
    from app import chat_store

    chat_store.start_turn(uid, fresh["id"], "hello", fresh["revision"], None)
    assert secret_swap.for_thread(uid, chats.resolve_thread_pk(uid, fresh["id"])) is None
    # A fork swaps like its source, with the same stand-ins and decisions.
    fork = chats.fork_thread(uid, swap.public_id)
    forked = secret_swap.for_thread(uid, chats.resolve_thread_pk(uid, fork["id"]))
    assert forked is not None
    assert forked.tokenize(f"T={key}") == seen


def test_unreadable_pairs_stop_the_turn_before_the_model_is_called(swap):
    from app import chat_store
    from tests.core.helpers import fake_provider, run_async, run_turn

    uid = swap.vault.user_id
    swap.tokenize("KEY=sk-proj-" + "Cz5Wk1Lp8Qv3Nx7Rb2Tm6Hy4" * 2)
    db.execute("UPDATE secret_tokens SET cipher='not-a-fernet-token' WHERE user_id=?", (uid,))
    secret_swap._vaults.clear()  # as after a restart with a changed APP_SECRET

    async def body():
        with fake_provider() as provider:  # any call to the model fails the test
            _, run = await run_turn(uid, "hello", thread=chats.get_thread(uid, swap.public_id))
        return run, provider.bodies

    run, bodies = run_async(body())
    assert bodies == []
    assert run["status"] == "failed" and "decrypted" in run["message"]["error"]
    assert chats.get_thread(uid, swap.public_id) is not None  # the chat still opens
    with pytest.raises(secret_swap.SwapUnavailable):
        secret_swap.for_thread(uid, swap.thread_id)


def test_with_the_site_switch_off_nothing_new_is_swapped_and_old_stand_ins_still_restore(fresh_db, monkeypatch):
    uid = quick_user()
    db.execute("UPDATE users SET chat_secret_swap=1 WHERE id=?", (uid,))
    old = chats.resolve_thread_pk(uid, chats.create_thread(uid, model="offline", tools=[])["id"])
    db.execute("UPDATE chat_threads SET secret_swap=1 WHERE id=?", (old,))
    key = "sk-proj-" + "Qx7Lm2Vn9Rt4Wp8Zk3Jh6Fd1Sb5Yc0Ga" * 2
    fake = secret_swap.for_thread(uid, old).tokenize(f"OPENAI_API_KEY={key}").split("=", 1)[1]
    assert fake != key
    monkeypatch.setattr(secret_swap.settings, "secret_swap_enabled", False)
    swap = secret_swap.for_thread(uid, old)
    assert swap.tokenize(f"again {key}") == f"again {key}"
    rows = [{"role": "user", "content": f"KEY={key}"}]
    assert swap.prepare(rows) is False and rows[0]["content"] == f"KEY={key}"
    assert swap.leaks(rows) == []
    assert swap.restore_args("run_terminal_command", json.dumps({"command": f"echo {fake}"})) == (
        json.dumps({"command": f"echo {key}"}), True)
    assert secret_swap.for_user(uid) is None
    new = chats.resolve_thread_pk(uid, chats.create_thread(uid, model="offline", tools=[])["id"])
    with db.tx() as conn:
        secret_swap.enable_for_new_thread(conn, {"chat_secret_swap": 1}, new, [])
    assert secret_swap.for_thread(uid, new) is None
