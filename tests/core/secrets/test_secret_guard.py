"""Secret protection's three doors as pure functions of a user's registry:
what goes to a provider holds stand-ins, what reaches a machine or the user
holds the real values, and text that is not a protected value is never
changed in either direction."""
import asyncio
import base64
import json
import pathlib
import random
from types import SimpleNamespace

import pytest

from app import api, db, secret_guard, secret_scan
from tests.core.helpers import quick_user, run_async

from ._values import ANTHROPIC, CUSTOM, GITHUB, HEX, NUMBER, OPENAI, PASSWORD, PEM, SHELLY, SLASHY, STRIPE, WORDS

REPO = pathlib.Path(__file__).resolve().parents[3]
ALL = {"OPENAI_API_KEY": OPENAI, "GITHUB_TOKEN": GITHUB, "STRIPE_KEY": STRIPE, "SESSION_SECRET": HEX,
       "DB_PASSWORD": PASSWORD, "ODD_PASSWORD": SHELLY, "SIGNING_KEY": PEM}


@pytest.fixture
def owner(fresh_db):
    uid = quick_user()
    for label, value in ALL.items():
        secret_guard.add_manual(uid, label, value)
    return uid, secret_guard.map_for(uid)


def fake_of(m, real):
    return next(it.fake for it in m.items if it.real == real)


def test_an_environment_secret_has_a_usable_name_one_value_and_is_always_masked(fresh_db):
    uid = quick_user()
    for bad in ("", "1KEY", "MY-KEY", "PATH", "ld_preload", "A" * 65):
        with pytest.raises(ValueError):
            secret_guard.add_manual(uid, bad, OPENAI, env=True)
    with pytest.raises(ValueError):
        secret_guard.add_manual(uid, "API_KEY", OPENAI + "\nmore", env=True)
    kept = secret_guard.add_manual(uid, "API_KEY", OPENAI, env=True)
    secret_guard.add_manual(uid, "API_KEY", GITHUB, env=True)   # a new value replaces the old one
    secret_guard.add_manual(uid, "plain", STRIPE)

    assert secret_guard.env_names(uid) == ["API_KEY"]
    assert secret_guard.env_values(uid, ["API_KEY", "plain", "NOPE"]) == {"API_KEY": GITHUB}
    assert secret_guard.reveal(uid, kept) is None
    assert secret_guard.env_request(uid, ["API_KEY", "API_KEY"]) == (["API_KEY"], "")
    assert secret_guard.env_request(uid, "plain")[1] and secret_guard.env_request(uid, [1])[1]

    # Switching protection off, or unticking, never sends an environment secret in clear.
    secret_guard.set_switches(uid, protect=False)
    env_id = next(i["id"] for i in secret_guard.overview(uid)["items"] if i["env"])
    assert not secret_guard.set_status(uid, env_id, False)
    m = secret_guard.map_for(uid)
    assert secret_guard.mask_text(m, f"token {GITHUB} and {STRIPE}") == f"token {fake_of(m, GITHUB)} and {STRIPE}"


def test_ordinary_text_is_byte_identical_through_both_doors(owner):
    """The regression test for the removed feature: with a realistic registry,
    no file of this repository changes on its way to a provider or a machine."""
    _uid, m = owner
    files = [p for folder in ("app", "static/chat", "tests/core", "docs", "scripts")
             for p in sorted((REPO / folder).rglob("*")) if p.suffix in (".py", ".js", ".md", ".sql", ".sh")]
    assert len(files) > 200
    for path in files:
        if path.parent.name == "secrets":
            continue  # these tests spell the values out
        text = path.read_text(errors="replace")
        assert secret_guard.mask_text(m, text) == text, path
        for kind in ("command", "content", "regex"):
            assert secret_guard.restore_field(m, text, kind) == (text, ""), path
        assert secret_guard.restore_text(m, text) == text, path


def test_round_trip_on_generated_documents(owner):
    _uid, m = owner
    rng = random.Random(7)
    filler = ["export", "KEY=", " ", "\n", '{"token": "', '"}', "curl -H 'Authorization: Bearer ", "'", "\\n", "://u:",
              "@host/db", "# note ", "value,", "(", ")", "héllo ", "id_42 ", "/srv/app/.env:3: "]
    values = [v for v in ALL.values() if v != PEM]
    for _ in range(300):
        parts, used = [], []
        for _ in range(rng.randint(1, 12)):
            if rng.random() < 0.4:
                used.append(rng.choice(values))
                parts.append(used[-1])
            else:
                parts.append(rng.choice(filler))
        doc = "".join(parts)
        masked = secret_guard.mask_text(m, doc)
        for value in set(used):
            assert value not in masked
        assert secret_guard.mask_text(m, masked) == masked
        assert secret_guard.restore_field(m, masked, "content") == (doc, "")


def test_every_spelling_of_a_strong_value_is_masked_and_restored(owner):
    _uid, m = owner
    fake = fake_of(m, OPENAI)
    b64 = base64.b64encode(OPENAI.encode()).decode()
    doc = json.dumps({"a": OPENAI, "b": f"x{OPENAI}y", "c": b64, "d": OPENAI.encode().hex()})
    masked = secret_guard.mask_text(m, doc)
    assert OPENAI not in masked and b64 not in masked and OPENAI.encode().hex() not in masked
    assert masked.count(fake) == 2 and json.loads(masked)["c"] == base64.b64encode(fake.encode()).decode()
    assert secret_guard.restore_field(m, masked, "content") == (doc, "")


def test_a_short_password_is_swapped_wherever_it_stands(owner):
    """mysql's -pPASSWORD puts nothing between the flag and the value: a
    password is not a word, so it needs no gap around it to be recognised."""
    _uid, m = owner
    fake = fake_of(m, PASSWORD)
    doc = f"mysqldump -uroot -p{PASSWORD} shop; psql postgres://app:{PASSWORD}@db/app  x{PASSWORD}9"
    masked = secret_guard.mask_text(m, doc)
    assert masked == f"mysqldump -uroot -p{fake} shop; psql postgres://app:{fake}@db/app  x{fake}9"
    assert secret_guard.restore_field(m, masked, "content") == (doc, "")


def test_a_value_that_reads_like_text_is_swapped_only_as_a_whole_word(fresh_db):
    """Words and numbers also occur inside other words and numbers, however
    long they are: only the whole value is taken for the secret."""
    uid = quick_user()
    secret_guard.add_manual(uid, "RATELIMIT_BYPASS_KEY", WORDS)
    secret_guard.add_manual(uid, "ACCOUNT_PIN", NUMBER)
    m = secret_guard.map_for(uid)
    words, number = fake_of(m, WORDS), fake_of(m, NUMBER)
    doc = f"X-Bypass: {WORDS}\nKEY='{WORDS}' pin={NUMBER}\nre{WORDS}s x{WORDS} {WORDS}2 order 9{NUMBER}0 id{NUMBER}"
    masked = secret_guard.mask_text(m, doc)
    assert masked == f"X-Bypass: {words}\nKEY='{words}' pin={number}\nre{WORDS}s x{WORDS} {WORDS}2 order 9{NUMBER}0 id{NUMBER}"
    assert secret_guard.restore_field(m, masked, "command") == (doc, "")
    assert secret_guard.restore_text(m, f"use {words}, not {words}x or 1{number}") == f"use {WORDS}, not {words}x or 1{number}"
    # Encoded, it is no longer text: a long enough one is found wherever it is.
    b64 = base64.b64encode(WORDS.encode()).decode()
    assert b64 not in secret_guard.mask_text(m, f"auth=x{b64}y")


def test_the_spellings_other_programs_write(fresh_db):
    """The same value as PHP's json_encode, a lower-case percent-encoder and
    JavaScript's encodeURIComponent write it."""
    uid = quick_user()
    bang = "Kq7!Zm2@Vx9*Lp4/Rt8"
    secret_guard.add_manual(uid, "AWS_SECRET_ACCESS_KEY", SLASHY)
    secret_guard.add_manual(uid, "DB_PASSWORD", bang)
    m = secret_guard.map_for(uid)
    spellings = [
        (SLASHY, lambda v: json.dumps({"secret": v}).replace("/", "\\/")),
        (SLASHY, lambda v: "sig=" + v.replace("/", "%2f").replace("+", "%2b").replace("=", "%3d")),
        (SLASHY, lambda v: "sig=" + v.replace("/", "%2F").replace("+", "%2B").replace("=", "%3D")),
        (bang, lambda v: "postgres://app:" + v.replace("@", "%40").replace("/", "%2F") + "@db/app"),   # encodeURIComponent
        (bang, lambda v: "u=" + v.replace("!", "%21").replace("@", "%40").replace("*", "%2A").replace("/", "%2F")),
    ]
    for real, spell in spellings:
        doc, want = spell(real), spell(fake_of(m, real))
        assert doc != want and secret_guard.mask_text(m, doc) == want
        assert secret_guard.restore_field(m, want, "content") == (doc, "")

def test_a_clipped_key_is_still_masked_but_never_restored(owner):
    _uid, m = owner
    fake = fake_of(m, OPENAI)
    head, tail = OPENAI[:30], OPENAI[-20:]
    masked = secret_guard.mask_text(m, f"log: {head}\n...{tail} end")
    assert masked == f"log: {fake[:30]}\n...{fake[-20:]} end"
    # A value with no public prefix is not head-matched: its start could be ordinary text.
    assert secret_guard.mask_text(m, f"id {HEX[:20]} x") == f"id {HEX[:20]} x"


def test_the_tool_door_restores_typed_fields_and_nothing_else(owner):
    uid, m = owner
    fake = fake_of(m, OPENAI)
    args = {"command": f"curl -H 'Authorization: Bearer {fake}' https://api.example.com", "cwd": f"/srv/{fake}"}
    assert secret_guard.restore_tool_args(uid, "run_terminal_command", args) == ""
    assert args == {"command": f"curl -H 'Authorization: Bearer {OPENAI}' https://api.example.com", "cwd": f"/srv/{fake}"}
    edit = {"path": f"/srv/{fake}", "old_string": f"KEY={fake}", "new_string": "KEY=x",
            "edits": [{"old_string": fake, "new_string": f"{fake}!"}]}
    assert secret_guard.restore_tool_args(uid, "search_replace", edit) == ""
    assert edit == {"path": f"/srv/{fake}", "old_string": f"KEY={OPENAI}", "new_string": "KEY=x",
                    "edits": [{"old_string": OPENAI, "new_string": f"{OPENAI}!"}]}
    web = {"query": f"is {fake} valid"}
    assert secret_guard.restore_tool_args(uid, "web_search", web) == "" and web == {"query": f"is {fake} valid"}
    plus = fake_of(m, STRIPE)
    pattern = {"pattern": f"KEY={plus}.*"}
    secret_guard.restore_tool_args(uid, "grep", pattern)
    assert pattern == {"pattern": f"KEY={STRIPE}.*"}


def test_a_cut_stand_in_never_reaches_a_machine(owner):
    uid, m = owner
    fake = fake_of(m, OPENAI)
    for broken in (fake[:40], fake[10:], fake[:20] + "..." + fake[-20:]):
        args = {"path": ".env", "content": f"OPENAI_API_KEY={broken}\n"}
        refusal = secret_guard.restore_tool_args(uid, "write_file", args)
        assert "OPENAI_API_KEY is incomplete" in refusal and args["content"] == f"OPENAI_API_KEY={broken}\n"
    # A short mention (as a model abbreviates a key in prose) is not a cut value.
    assert secret_guard.restore_tool_args(uid, "write_file", {"path": "n.md", "content": f"key {fake[:14]}…"}) == ""


def test_a_value_with_shell_characters_is_not_placed_in_a_command(owner):
    uid, m = owner
    fake = fake_of(m, SHELLY)
    args = {"command": f'mysql -p"{fake}" app'}
    refusal = secret_guard.restore_tool_args(uid, "run_terminal_command", args)
    assert "ODD_PASSWORD contains shell characters" in refusal and args == {"command": f'mysql -p"{fake}" app'}
    # A file is not a shell: it gets the real value.
    file = {"path": "my.cnf", "content": f"password={fake}\n"}
    assert secret_guard.restore_tool_args(uid, "write_file", file) == "" and file["content"] == f"password={SHELLY}\n"


def test_a_reply_streamed_in_any_pieces_restores_to_the_same_text(owner):
    _uid, m = owner
    reply = f"Use {fake_of(m, OPENAI)} and {fake_of(m, PASSWORD)}, not x{fake_of(m, PASSWORD)}. Done {fake_of(m, GITHUB)}"
    want = secret_guard.restore_text(m, reply)
    assert OPENAI in want and GITHUB in want and want.count(PASSWORD) == 2
    for cut in range(len(reply) + 1):
        stream = secret_guard.StreamRestorer(m)
        assert stream.push(reply[:cut]) + stream.push(reply[cut:]) + stream.flush() == want
    rng = random.Random(3)
    for _ in range(50):
        stream, out, at = secret_guard.StreamRestorer(m), "", 0
        while at < len(reply):
            step = rng.randint(1, 9)
            out += stream.push(reply[at:at + step])
            at += step
        assert out + stream.flush() == want


def test_only_text_a_provider_may_rewrite_is_masked(owner):
    _uid, m = owner
    fake = fake_of(m, OPENAI)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": f"key {OPENAI}"}, {"type": "image_url", "image_url": {"url": OPENAI}}]},
        {"role": "assistant", "content": "ok", "reasoning_content": OPENAI, "claude_thinking": [{"thinking": OPENAI, "signature": "s"}],
         "tool_calls": [{"id": OPENAI, "type": "function", "function": {"name": "run", "arguments": json.dumps({"c": OPENAI})}}]},
        {"role": "tool", "tool_call_id": OPENAI, "content": f"out {OPENAI}"},
    ]
    out = secret_guard.mask_value(m, messages)
    assert out[0]["content"][0]["text"] == f"key {fake}" and out[0]["content"][1] is messages[0]["content"][1]
    assert out[1]["reasoning_content"] == OPENAI and out[1]["claude_thinking"] is messages[1]["claude_thinking"]
    assert out[1]["tool_calls"][0]["id"] == OPENAI and json.loads(out[1]["tool_calls"][0]["function"]["arguments"]) == {"c": fake}
    assert out[2] == {"role": "tool", "tool_call_id": OPENAI, "content": f"out {fake}"}
    clean = [{"role": "user", "content": "nothing here"}]
    assert secret_guard.mask_value(m, clean) is clean


def test_the_provider_door_masks_every_attempt_and_spares_api_clients(owner):
    uid, m = owner
    seen = []

    class Source:
        async def chat(self, body, **_kwargs):
            seen.append(body)
            return "result"

    body = {"model": "x", "messages": [{"role": "user", "content": f"deploy with {OPENAI}"}]}
    assert run_async(api._run_chat(Source(), body, uid)) == "result"
    assert run_async(api._run_chat(Source(), body, uid, mask=False)) == "result"
    assert seen[0]["messages"][0]["content"] == f"deploy with {fake_of(m, OPENAI)}"
    assert seen[1] is body and body["messages"][0]["content"] == f"deploy with {OPENAI}"


def test_offers_are_decided_once_and_apply_to_every_chat(fresh_db):
    uid, other = quick_user(), quick_user("other@core.test")
    text = f"OPENAI_API_KEY={OPENAI}\nDB_PASSWORD={PASSWORD}\nPORT=3000\n# {GITHUB}\n"
    found = secret_scan.find(".env", text)
    assert secret_guard.offer(uid, 3, "box", found) == 3 and secret_guard.offer(uid, 3, "box", found) == 0
    rows = secret_guard.offers(uid)
    assert [(r["key"], r["value"], r["file"], r["line"], r["host"]) for r in rows] == [
        ("OPENAI_API_KEY", OPENAI, ".env", 1, "box"), ("DB_PASSWORD", PASSWORD, ".env", 2, "box"), ("", GITHUB, ".env", 4, "box")]
    assert secret_guard.offers(other) == [] and not secret_guard.map_for(uid).masks  # nothing is swapped before a decision
    assert secret_guard.decide(uid, [rows[0]["id"], rows[2]["id"]]) == 2
    m = secret_guard.map_for(uid)
    assert {it.real for it in m.items} == {OPENAI, GITHUB} and not secret_guard.has_offers(uid)
    # The declined value is dropped (only its fingerprint stays) and is not offered again.
    kept = db.fetchone("SELECT cipher, fake FROM user_secrets WHERE user_id=? AND status='ignored'", (uid,))
    assert (kept["cipher"], kept["fake"]) == ("", "")
    assert secret_guard.offer(uid, 3, "box", found) == 0 and secret_guard.offers(uid) == []
    assert not secret_guard.map_for(other).items


def test_the_owner_is_asked_once_per_secret(fresh_db):
    uid = quick_user()

    def scan(text, path=".env"):
        return secret_guard.offer(uid, 3, "box", secret_scan.find(path, text))

    def protected():
        return {it.real for it in secret_guard.map_for(uid).items if it.masked}

    first = f"OPENAI_API_KEY={OPENAI}\nDB_PASSWORD={PASSWORD}\n"
    assert scan(first) == 2
    key, _password = secret_guard.offers(uid)
    secret_guard.decide(uid, [key["id"]])  # protect the key, decline the password
    # The same files in any later chat, or with their lines moved: nothing to ask.
    assert scan(first) == 0 and scan("# moved\n\n" + first) == 0 and not secret_guard.has_offers(uid)
    # The protected key is rotated on disk: its new value is protected at once, unasked,
    # the old one stays protected (old output still holds it), and the chat is told once.
    assert scan(f"OPENAI_API_KEY={ANTHROPIC}\nDB_PASSWORD={PASSWORD}\n") == 0
    assert protected() == {OPENAI, ANTHROPIC} and not secret_guard.has_offers(uid)
    assert secret_guard.take_notes(uid) == ["OPENAI_API_KEY changed in .env: Fuse protects its new value too."]
    assert secret_guard.take_notes(uid) == []
    # The declined password changes: that decision was about the old value, so the owner is asked about the new one.
    changed = f"OPENAI_API_KEY={ANTHROPIC}\nDB_PASSWORD=N3w&Pass9word\n"
    assert scan(changed) == 1 and scan(changed) == 0
    assert [(o["key"], o["value"]) for o in secret_guard.offers(uid)] == [("DB_PASSWORD", "N3w&Pass9word")]
    secret_guard.decide(uid, [])
    assert scan(changed) == 0 and not secret_guard.has_offers(uid)
    # The value it replaced is forgotten with it: a name that keeps changing holds one row, not one per value.
    declined = db.fetchall("SELECT cipher, fake FROM user_secrets WHERE user_id=? AND status='ignored'", (uid,))
    assert [(r["cipher"], r["fake"]) for r in declined] == [("", "")] and protected() == {OPENAI, ANTHROPIC}
    # Only something never decided asks: a new name here, or the same name in another project's file.
    assert scan(f"OPENAI_API_KEY={ANTHROPIC}\nSTRIPE_KEY={STRIPE}\n") == 1
    assert scan(f"OPENAI_API_KEY={GITHUB}\n", path="other/.env") == 1
    assert [(o["key"], o["file"]) for o in secret_guard.offers(uid)] == [("STRIPE_KEY", ".env"), ("OPENAI_API_KEY", "other/.env")]


def test_a_decision_belongs_to_its_project(fresh_db):
    """Two projects on one machine both have a ".env": what the owner decided
    for a name in one says nothing about the other's."""
    uid = quick_user()

    def scan(root, text):
        return secret_guard.offer(uid, 3, "box", secret_scan.find(".env", text), root)

    def protected():
        return {it.real for it in secret_guard.map_for(uid).items if it.masked}

    assert scan("/srv/shop", f"API_TOKEN={STRIPE}\nDB_PASSWORD={PASSWORD}\n") == 2
    token, _password = secret_guard.offers(uid)
    secret_guard.decide(uid, [token["id"]])
    # The same names with other values in another project: asked, and until then neither protected nor declined.
    assert scan("/srv/blog", f"API_TOKEN={HEX}\nDB_PASSWORD={SHELLY}\n") == 2
    assert protected() == {STRIPE} and secret_guard.take_notes(uid) == []
    assert {o["value"] for o in secret_guard.offers(uid)} == {HEX, SHELLY}
    secret_guard.decide(uid, [])
    # In its own project a protected name still follows its key.
    assert scan("/srv/shop", f"API_TOKEN={GITHUB}\nDB_PASSWORD={PASSWORD}\n") == 0 and protected() == {STRIPE, GITHUB}
    assert scan("/srv/blog", f"API_TOKEN={ANTHROPIC}\n") == 1 and ANTHROPIC not in protected()


def test_a_value_kept_before_places_had_a_folder_is_not_asked_about_again(fresh_db):
    uid = quick_user()
    found = secret_scan.find(".env", f"API_TOKEN={STRIPE}\n")
    secret_guard.offer(uid, 3, "box", found, "/srv/shop")
    secret_guard.decide(uid, [secret_guard.offers(uid)[0]["id"]])
    row = db.fetchone("SELECT id, sources FROM user_secrets WHERE user_id=?", (uid,))
    old = [{k: v for k, v in src.items() if k != "root"} for src in json.loads(row["sources"])]
    db.execute("UPDATE user_secrets SET sources=? WHERE id=?", (json.dumps(old), row["id"]))

    assert secret_guard.offer(uid, 3, "box", found, "/srv/shop") == 0
    sources = json.loads(db.fetchone("SELECT sources FROM user_secrets WHERE id=?", (row["id"],))["sources"])
    assert [(s["root"], s["path"], s["key"]) for s in sources] == [("/srv/shop", ".env", "API_TOKEN")]
    # From here on a rotated key in that project is protected unasked again.
    assert secret_guard.offer(uid, 3, "box", secret_scan.find(".env", f"API_TOKEN={GITHUB}\n"), "/srv/shop") == 0
    assert GITHUB in {it.real for it in secret_guard.map_for(uid).items if it.masked}


def test_an_offer_says_which_values_read_like_text(fresh_db):
    uid = quick_user()
    text = f"RATELIMIT_BYPASS_KEY={WORDS}\nMETRICS_TOKEN={CUSTOM}\nDB_PASSWORD={PASSWORD}\n"
    secret_guard.offer(uid, 3, "box", secret_scan.find(".env", text), "/srv/shop")
    assert [(o["key"], o["plain"]) for o in secret_guard.offers(uid)] == [
        ("RATELIMIT_BYPASS_KEY", True), ("METRICS_TOKEN", False), ("DB_PASSWORD", False)]


def test_a_whole_session_leaves_with_no_protected_value_in_it(fresh_db):
    """Everything one coding session sends: the project's instructions, what
    the user pasted, the model's own calls and what the tools printed. No
    protected value is in the request, and each text comes back unchanged."""
    uid = quick_user()
    kept = {"OPENAI_API_KEY": OPENAI, "GH_TOKEN": GITHUB, "STRIPE_SECRET_KEY": STRIPE, "SECRET_KEY": HEX,
            "AWS_SECRET_ACCESS_KEY": SLASHY, "METRICS_TOKEN": CUSTOM, "DB_PASSWORD": PASSWORD,
            "RATELIMIT_BYPASS_KEY": WORDS, "SIGNING_KEY": PEM}
    for label, value in kept.items():
        secret_guard.add_manual(uid, label, value)
    m = secret_guard.map_for(uid)
    env = "".join(f"{name}={value}\n" for name, value in kept.items() if value != PEM)
    k8s = base64.b64encode(OPENAI.encode()).decode()
    texts = [
        f"You are a coding agent.\n# AGENTS.md\nDeploy with STRIPE_SECRET_KEY={STRIPE} (ask ops for a new one).",
        f"the build fails with this env, why?\n\n{env}\nand prod uses postgres://shop:{PASSWORD}@db.internal:5432/shop",
        "".join(f"{n}: {line}\n" for n, line in enumerate(env.splitlines(), 1)),
        f"$ env | sort\nHOME=/home/dev\nOPENAI_API_KEY={OPENAI}\nPATH=/usr/bin\n$ mysqldump -ushop -p{PASSWORD} shop > /tmp/shop.sql",
        f"> GET /v1/models HTTP/2\n> authorization: Bearer {OPENAI}\n> x-bypass: {WORDS}\n< HTTP/2 200",
        json.dumps({"services": {"api": {"environment": [f"SECRET_KEY={HEX}", f"GH_TOKEN={GITHUB}"]}},
                    "aws": {"secret": SLASHY}, "metrics": {"token": CUSTOM}}, indent=2),
        f"apiVersion: v1\nkind: Secret\ndata:\n  openai: {k8s}\n  hex: {HEX.encode().hex()}",
        f"-METRICS_TOKEN=old\n+METRICS_TOKEN={CUSTOM}\n {PEM}\nremote: https://x-access-token:{GITHUB}@github.com/acme/shop.git",
        f"Traceback (most recent call last):\n  stripe.error.AuthenticationError: Invalid API Key provided: {STRIPE[:12]}{'*' * 12}{STRIPE[-4:]}",
    ]
    calls = [{"id": "call-1", "type": "function", "function": {
        "name": "run_terminal_command", "arguments": json.dumps({"command": f"mysql -ushop -p{PASSWORD} -e 'select 1' && curl -H 'X-Key: {CUSTOM}' https://api.internal/"})}}]
    messages = [{"role": "system", "content": texts[0]}, {"role": "user", "content": [{"type": "text", "text": texts[1]}]},
                {"role": "assistant", "content": "", "tool_calls": calls}]
    messages += [{"role": "tool", "tool_call_id": "call-1", "content": text} for text in texts[2:]]
    body = {"model": "x", "messages": messages}

    sent = run_async(secret_guard.outbound(uid, body))
    wire = json.dumps(sent, ensure_ascii=False)
    for name, value in kept.items():
        probe = value.splitlines()[1] if value == PEM else value
        assert probe not in wire and json.dumps(probe)[1:-1] not in wire, name
    assert k8s not in wire and HEX.encode().hex() not in wire
    # The masked half of a key in an error message stays as it was: nothing whole to swap.
    assert STRIPE[:12] + "*" * 12 in wire
    # Nothing but the values changed, and every text restores to exactly what it was.
    plain_wire = json.dumps(body, ensure_ascii=False)
    assert wire.count(fake_of(m, OPENAI)) == plain_wire.count(OPENAI) == 4 and len(wire) == len(plain_wire)
    back = [m_["content"] if isinstance(m_["content"], str) else m_["content"][0]["text"] for m_ in sent["messages"]]
    for masked, original in zip([back[0], back[1], *back[3:]], texts):
        assert secret_guard.restore_field(m, masked, "content") == (original, "")
    assert body["messages"][0]["content"] == texts[0]   # the stored chat itself is untouched


@pytest.mark.parametrize("real,fake,anywhere", [
    ("Tr0ub4dor&3xq", "Kp7ax2mwe&9bK", True),     # ends as it starts: its last character could begin it again
    ("482915730264", "697057194156", False),
])
def test_a_stand_in_whose_end_reads_like_its_start_is_not_cut_in_two(real, fake, anywhere):
    m = secret_guard.SecretMap([secret_guard.Item(1, "X", real, fake, False, 0, True, True, anywhere=anywhere)])
    for reply in (f"{fake}{fake[1]} then", f"{fake}{fake[1:4]}", f"a {fake}{fake[1:3]} {fake} {fake}", f"{fake}{fake}"):
        want = secret_guard.restore_text(m, reply)
        for cut in range(len(reply) + 1):
            for second in range(cut, len(reply) + 1):
                stream = secret_guard.StreamRestorer(m)
                got = stream.push(reply[:cut]) + stream.push(reply[cut:second]) + stream.push(reply[second:]) + stream.flush()
                assert got == want, (reply, cut, second)


def test_a_reply_streamed_in_any_pieces_with_many_values_restores_like_the_whole(fresh_db):
    uid = quick_user()
    for i, value in enumerate([OPENAI, GITHUB, PASSWORD, WORDS, NUMBER, CUSTOM, SLASHY, "aB3xY9kQ"]):
        secret_guard.add_manual(uid, f"VALUE_{i}", value)
    m = secret_guard.map_for(uid)
    fakes = [it.fake for it in m.items]
    glue = [" ", "x", "9", "_", "\n", "=", "'", "é", fakes[0][:11], fakes[3][:-1], fakes[4][1:], fakes[7][:4]]
    rng = random.Random(11)
    for _ in range(400):
        reply = "".join(rng.choice(fakes + glue) for _ in range(rng.randint(1, 14)))
        want = secret_guard.restore_text(m, reply)
        stream, out, at = secret_guard.StreamRestorer(m), "", 0
        while at < len(reply):
            step = rng.randint(1, 7)
            out += stream.push(reply[at:at + step])
            at += step
        assert out + stream.flush() == want, reply


def test_switching_protection_off_keeps_old_chats_working(owner):
    uid, m = owner
    fake = fake_of(m, OPENAI)
    secret_guard.set_switches(uid, protect=False)
    off = secret_guard.map_for(uid)
    assert secret_guard.mask_text(off, f"key {OPENAI}") == f"key {OPENAI}"
    assert secret_guard.restore_field(off, f"key {fake}", "command") == (f"key {OPENAI}", "")
    one = next(i["id"] for i in secret_guard.overview(uid)["items"] if i["label"] == "GITHUB_TOKEN")
    secret_guard.set_switches(uid, protect=True)
    assert secret_guard.set_status(uid, one, False)
    m = secret_guard.map_for(uid)
    assert secret_guard.mask_text(m, GITHUB) == GITHUB and secret_guard.restore_text(m, fake_of(m, GITHUB)) == GITHUB


def test_values_that_would_replace_ordinary_text_are_refused(fresh_db):
    uid = quick_user()
    for bad in ("short", "aaaaaaaaaaaa", "--------"):
        with pytest.raises(ValueError):
            secret_guard.add_manual(uid, "X", bad)
    secret_guard.add_manual(uid, "A", OPENAI)
    with pytest.raises(ValueError):
        secret_guard.add_manual(uid, "B", OPENAI[5:40])  # overlaps a kept value


def test_unreadable_values_stop_model_calls_until_reset(owner):
    uid, _m = owner
    db.execute("UPDATE user_secrets SET cipher='not-a-token' WHERE user_id=?", (uid,))
    secret_guard._changed(uid)
    assert secret_guard.map_for(uid).broken and secret_guard.overview(uid)["broken"]
    request = SimpleNamespace(scope={})
    with pytest.raises(api.HTTPException) as caught:
        run_async(api._invoke_chat(request, {"id": uid}, {"model": "offline", "messages": [{"role": "user", "content": "hi"}]}))
    assert caught.value.status_code == 409 and "Secret protection" in caught.value.detail
    secret_guard.delete(uid)
    assert not secret_guard.map_for(uid).broken


def test_a_waiting_turn_resumes_when_the_owner_decides(fresh_db):
    uid = quick_user()
    secret_guard.offer(uid, 1, "box", secret_scan.find(".env", f"API_TOKEN={STRIPE}\n"))

    async def body():
        wait = asyncio.ensure_future(secret_guard.wait_for_decision(uid, 42))
        for _ in range(200):
            if secret_guard.waiting(uid, 42):
                break
            await asyncio.sleep(0.01)
        assert secret_guard.waiting(uid, 42) and not secret_guard.waiting(uid, 43)
        await asyncio.to_thread(secret_guard.decide, uid, [secret_guard.offers(uid)[0]["id"]])
        return await asyncio.wait_for(wait, 5)

    assert run_async(body()) == "decided" and not secret_guard.waiting(uid, 42)
    assert run_async(secret_guard.wait_for_decision(uid, 42, timeout=0.05)) == "decided"  # nothing left to decide
    secret_guard.offer(uid, 1, "box", secret_scan.find(".env", f"OTHER_TOKEN={OPENAI}\n"))
    assert run_async(secret_guard.wait_for_decision(uid, 42, timeout=0.05)) == "timeout"
    # With detection switched off no turn waits, whatever is still undecided.
    secret_guard.set_switches(uid, offer_on=False)
    assert not secret_guard.has_offers(uid)
    assert run_async(secret_guard.wait_for_decision(uid, 42, timeout=0.05)) == "decided"
