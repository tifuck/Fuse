"""Cross-user isolation over HTTP: a signed-in user reaching for another
account's object by id is refused AND changes nothing, and the owner's same
request works (so a refusal is ownership, not a route that refuses everyone)."""
import importlib
import re
from unittest.mock import AsyncMock, patch

import pytest

from app import accounts, artifacts, chat_store, chats, db, hosts, memory, permissions, schedules, vault
from tests.core.auth._support import PASSWORD, http_routes, sign_in
from tests.core.helpers import make_user, quick_user


@pytest.fixture
def alice(fresh_db):
    return quick_user("alice@core.test")


@pytest.fixture
def bob(fresh_db):
    return quick_user("bob@core.test")


def _thread_state(uid, public_id):
    thread = chats.get_thread(uid, public_id)
    return thread["title"], thread["revision"], len(thread["messages"])


def test_threads_and_runs_are_private_to_their_owner(web, alice, bob):
    thread = chats.create_thread(alice, model="offline", title="Alice thread", context="alice-private-context")
    tid = thread["id"]
    run = chat_store.start_turn(alice, tid, "hello", thread["revision"], "turn-1")["run"]
    child = chat_store.start_child_run(user_id=alice, thread_id=tid, parent_run_id=run["id"], prompt="audit",
                                       description="worker", background=True)
    rev = chats.get_thread(alice, tid)["revision"]
    before = _thread_state(alice, tid)
    runs_before = db.fetchall("SELECT id, status, cancel_requested FROM chat_runs ORDER BY id")

    sign_in(web, bob)
    attempts = [
        ("GET", f"/chat/threads/{tid}", None),
        ("PATCH", f"/chat/threads/{tid}", {"title": "pwned", "expected_revision": rev, "command_id": "b1"}),
        ("DELETE", f"/chat/threads/{tid}", {"expected_revision": rev, "command_id": "b2"}),
        ("POST", f"/chat/threads/{tid}/turns", {"text": "pwned", "expected_revision": rev, "command_id": "b3"}),
        ("POST", f"/chat/threads/{tid}/queue", {"text": "pwned", "id": "b4"}),
        ("DELETE", f"/chat/threads/{tid}/queue/1", None),
        ("POST", f"/chat/threads/{tid}/steer", {"text": "pwned", "command_id": "b5"}),
        ("POST", f"/chat/threads/{tid}/title", {}),
        ("POST", f"/chat/threads/{tid}/recap", {}),
        ("POST", f"/chat/threads/{tid}/compact", {"expected_revision": rev, "command_id": "b6"}),
        ("POST", f"/chat/threads/{tid}/fork", {"expected_revision": rev, "command_id": "b7"}),
        ("POST", f"/chat/threads/{tid}/project",
         {"host_id": None, "project_id": None, "expected_revision": rev, "command_id": "b8"}),
        ("GET", f"/chat/threads/{tid}/agents", None),
        ("GET", f"/chat/threads/{tid}/jobs", None),
        ("POST", f"/chat/threads/{tid}/jobs/job-1/kill", None),
        ("GET", f"/chat/threads/{tid}/permissions", None),
        ("POST", f"/chat/threads/{tid}/permissions/ask-1", {"action": "deny"}),
        ("GET", f"/chat/threads/{tid}/project-agents", None),
        ("GET", f"/chat/runs/{run['id']}", None),
        ("GET", f"/chat/runs/{run['id']}/images/1", None),
        ("POST", f"/chat/runs/{run['id']}/cancel", None),
        ("POST", f"/chat/runs/{run['id']}/resume", None),
        ("POST", f"/chat/runs/{child['id']}/message", {"text": "pwned"}),
    ]
    answers = {}
    for method, path, body in attempts:
        res = web.request(method, path, json=body)
        answers[(method, path)] = res.status_code
        assert "Alice thread" not in res.text and "alice-private-context" not in res.text, path
    assert {k: v for k, v in answers.items() if v != 404} == {}
    # Bob's own views do not show it either.
    assert "Alice thread" not in web.get("/chat/threads").text
    assert "alice-private-context" not in web.get("/chat/context", params={"thread_id": tid}).text

    assert _thread_state(alice, tid) == before
    assert db.fetchall("SELECT id, status, cancel_requested FROM chat_runs ORDER BY id") == runs_before
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_threads WHERE user_id=?", (bob,))["n"] == 0

    sign_in(web, alice)
    assert web.get(f"/chat/threads/{tid}").json()["title"] == "Alice thread"
    assert web.get(f"/chat/runs/{run['id']}").status_code == 200
    renamed = web.patch(f"/chat/threads/{tid}", json={"title": "Renamed", "expected_revision": rev, "command_id": "a1"})
    assert renamed.status_code == 200, renamed.text


def _alice_machine(uid):
    vault.unlock(uid, PASSWORD)
    host = hosts.add_host(uid, {"alias": "alpha", "hostname": "a.example.com", "username": "ada",
                                "auth_type": "key", "secret": "SECRET-KEY-MATERIAL", "passphrase": "PASSPHRASE-x"})
    project = hosts.add_project(uid, host["id"], {"name": "site", "remote_path": "/srv/site"})
    return host, project


def test_hosts_and_projects_are_private_to_their_owner(web, bob):
    alice = make_user("alice@core.test")
    host, project = _alice_machine(alice)
    hid, pid = host["id"], project["id"]
    pairing = hosts.mint_pairing(alice, "laptop")
    rows_before = db.fetchall("SELECT * FROM user_hosts ORDER BY id"), db.fetchall("SELECT * FROM user_projects ORDER BY id")
    logo = {"data": "data:image/png;base64,iVBORw0KGgo="}

    sign_in(web, bob)
    attempts = [
        ("PATCH", f"/hosts/{hid}", {"label": "pwned"}),
        ("DELETE", f"/hosts/{hid}", None),
        ("POST", f"/hosts/{hid}/projects", {"name": "evil", "remote_path": "/tmp"}),
        ("PATCH", f"/hosts/{hid}/projects/{pid}", {"name": "pwned"}),
        ("DELETE", f"/hosts/{hid}/projects/{pid}", None),
        ("PUT", f"/hosts/{hid}/projects/{pid}/logo", logo),
        ("DELETE", f"/hosts/{hid}/projects/{pid}/logo", None),
        ("GET", f"/projects/{pid}/logo", None),
        ("POST", f"/hosts/{hid}/probe", None),
        ("POST", f"/hosts/{hid}/test", None),
        ("POST", f"/hosts/{hid}/trust", {"algorithm": "ssh-ed25519", "fingerprint": "cd" * 32}),
        ("POST", f"/hosts/{hid}/browse", {"path": "/"}),
        ("POST", f"/hosts/{hid}/mkdir", {"path": "/", "name": "x"}),
        ("DELETE", f"/hosts/pairings/{pairing['id']}", None),
    ]
    answers = {}
    for method, path, body in attempts:
        res = web.request(method, path, json=body)
        answers[(method, path)] = res.status_code
        assert "SECRET-KEY-MATERIAL" not in res.text and "a.example.com" not in res.text, path
        if path.endswith(("/browse", "/mkdir")):
            # The folder picker reports the lookup miss as its own 400.
            assert res.status_code in (400, 404) and "host not found" in res.text, res.text
            answers.pop((method, path))
    assert {k: v for k, v in answers.items() if v != 404} == {}
    for listing in ("/hosts/api", "/projects/api", "/hosts/pairings"):
        text = web.get(listing).text
        assert "alpha" not in text and "/srv/site" not in text and pairing["code"] not in text, listing

    # Bob cannot point his own chats at her project either.
    created = web.post("/chat/threads", json={"model": "offline", "command_id": "b1", "host_id": hid, "project_id": pid})
    # A foreign project is dropped, not bound: the chat starts as a plain one.
    assert created.status_code == 201 and (created.json()["host_id"], created.json()["project_id"]) == (None, None)
    own = chats.create_thread(bob, model="offline")
    moved = web.post(f"/chat/threads/{own['id']}/project",
                     json={"host_id": hid, "project_id": pid, "expected_revision": own["revision"], "command_id": "b2"})
    assert moved.status_code in (403, 404, 422), moved.text
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_threads WHERE project_id=?", (pid,))["n"] == 0

    assert (db.fetchall("SELECT * FROM user_hosts ORDER BY id"), db.fetchall("SELECT * FROM user_projects ORDER BY id")) == rows_before
    assert [p["id"] for p in hosts.list_pending_pairings(alice)] == [pairing["id"]]

    sign_in(web, alice)
    assert web.patch(f"/hosts/{hid}", json={"label": "mine"}).status_code == 200
    assert web.patch(f"/hosts/{hid}/projects/{pid}", json={"name": "renamed"}).status_code == 200


def test_host_payloads_never_carry_the_secret_or_passphrase(web):
    alice = make_user("alice@core.test")
    sign_in(web, alice)
    vault.unlock(alice, PASSWORD)
    created = web.post("/hosts", json={"alias": "alpha", "hostname": "a.example.com", "username": "ada",
                                       "auth_type": "key", "secret": "SECRET-KEY-MATERIAL", "passphrase": "PASSPHRASE-x"})
    assert created.status_code == 201, created.text
    for res in (created, web.get("/hosts/api"), web.get("/projects/api"), web.get("/hosts"),
                web.patch(f"/hosts/{created.json()['id']}", json={"label": "x"})):
        assert res.status_code in (200, 201)
        assert "SECRET-KEY-MATERIAL" not in res.text and "PASSPHRASE-x" not in res.text
    for host in web.get("/hosts/api").json()["hosts"]:
        assert "secret" not in host and "passphrase" not in host


def test_artifacts_are_private_to_their_owner(web, alice, bob):
    private = artifacts.create(alice, title="Private", html="<p>secret sauce</p>")["identifier"]
    public = artifacts.create(alice, title="Public", html="<p>shared</p>", visibility="public")["identifier"]
    bob_thread = chats.create_thread(bob, model="offline")["id"]
    alice_thread = chats.create_thread(alice, model="offline")["id"]
    own = artifacts.create(bob, title="Bob's", html="<p>bob</p>")["identifier"]
    rows_before = db.fetchall("SELECT * FROM artifacts ORDER BY id")

    def owner_ops(ident, thread):
        return [
            ("GET", f"/a/{ident}/source", None),
            ("GET", f"/a/{ident}/diff?from=1", None),
            ("GET", f"/a/{ident}/download", None),
            ("POST", f"/a/{ident}/visibility", {"visibility": "public"}),
            ("POST", f"/a/{ident}/title", {"title": "pwned"}),
            ("POST", f"/a/{ident}/pin", {"version": 1}),
            ("POST", f"/a/{ident}/rotate-link", {}),
            ("POST", f"/a/{ident}/revert", {"version": 1}),
            ("POST", f"/a/{ident}/attach", {"thread_id": thread}),
            ("POST", f"/a/{ident}/continue", {}),
            ("POST", f"/a/{ident}/archive", {}),
            ("POST", f"/a/{ident}/restore", {}),
            ("POST", f"/a/{ident}/delete", {}),
        ]

    sign_in(web, bob)
    answers = {}
    for method, path, body in [
        ("GET", f"/a/{private}", None), ("GET", f"/a/{private}/content", None), ("GET", f"/a/{private}/meta", None),
        *owner_ops(private, bob_thread), *owner_ops(public, bob_thread),
    ]:
        res = web.request(method, path, json=body, follow_redirects=False)
        answers[(method, path)] = res.status_code
        assert "secret sauce" not in res.text, path
    assert {k: v for k, v in answers.items() if v != 404} == {}
    # A public artifact is viewable by design; it is the shared page only.
    assert web.get(f"/a/{public}/content").status_code == 200
    assert web.get(f"/a/{public}/meta").json()["owner"] is False
    # His own artifact cannot be filed into her chat.
    assert web.post(f"/a/{own}/attach", json={"thread_id": alice_thread}).status_code == 404
    assert "Private" not in web.get("/artifacts/list").text
    assert db.fetchall("SELECT * FROM artifacts ORDER BY id") == rows_before

    sign_in(web, alice)
    for method, path, body in owner_ops(private, alice_thread)[:-3]:
        res = web.request(method, path, json=body, follow_redirects=False)
        assert res.status_code < 400, (method, path, res.status_code, res.text[:200])


def test_schedules_are_private_to_their_owner(web, bob):
    alice = make_user("alice@core.test")
    _host, project = _alice_machine(alice)
    item = schedules.create_schedule(alice, {"name": "nightly", "prompt": "check", "model": "offline",
                                             "recur_kind": "every", "every_seconds": 3600})
    before = db.fetchall("SELECT * FROM chat_schedules ORDER BY id")

    sign_in(web, bob)
    answers = {}
    for token in (item["public_id"], str(item["id"])):
        for method, path, body in [
            ("GET", f"/schedules/{token}", None),
            ("GET", f"/schedules/{token}/fires", None),
            ("PATCH", f"/schedules/{token}", {"name": "pwned"}),
            ("POST", f"/schedules/{token}/run", None),
            ("DELETE", f"/schedules/{token}", None),
        ]:
            res = web.request(method, path, json=body)
            answers[(method, path)] = res.status_code
            assert "nightly" not in res.text
    assert {k: v for k, v in answers.items() if v != 404} == {}
    assert web.get("/schedules").json()["schedules"] == []
    aimed = web.post("/schedules", json={"name": "x", "prompt": "p", "model": "offline", "recur_kind": "every",
                                         "every_seconds": 3600, "target": "project", "project_id": project["id"]})
    assert aimed.status_code == 404, aimed.text
    assert db.fetchall("SELECT * FROM chat_schedules ORDER BY id") == before

    sign_in(web, alice)
    assert web.get(f"/schedules/{item['public_id']}").status_code == 200
    assert web.patch(f"/schedules/{item['public_id']}", json={"name": "renamed"}).status_code == 200


def test_api_keys_cannot_be_revoked_or_seen_by_another_account(web, alice, bob):
    raw = accounts.create_api_key(alice, "alice-ci-key")
    key_id = accounts.keys_for(alice)[-1]["id"]
    sign_in(web, bob)
    deleted = web.post(f"/keys/{key_id}/delete", follow_redirects=False)
    assert deleted.status_code == 303 and deleted.headers["location"] == "/keys"
    assert accounts.user_by_api_key(raw)["id"] == alice
    assert "alice-ci-key" not in web.get("/keys").text

    sign_in(web, alice)
    web.post(f"/keys/{key_id}/delete", follow_redirects=False)
    assert accounts.user_by_api_key(raw) is None


_REVOKE = re.compile(r"^/(?:sources|pool)/(\w+)/\{cred_id\}/revoke$")


def _revoke_paths():
    routes, _mounts, _sockets = http_routes()
    return sorted(path for method, path in routes if method == "POST" and _REVOKE.match(path))


def test_every_pool_has_a_revoke_route():
    kinds = {_REVOKE.match(path).group(1) for path in _revoke_paths()}
    assert {"grok", "chatgpt", "claude", "cursor", "devin", "kimi", "mimo", "nanogpt", "openrouter",
            "refuseless", "zai", "explabs"} <= kinds


@pytest.mark.parametrize("path", _revoke_paths())
def test_only_the_donor_or_an_admin_can_remove_a_pool_credential(web, path, alice, bob):
    kind = _REVOKE.match(path).group(1)
    source_id = getattr(importlib.import_module(f"app.sources.{kind}"), f"ensure_{kind}_source")()
    admin = quick_user("root@core.test", admin=True)
    creds = [accounts.add_credential(source_id, "alice", "not-a-real-secret", donated_by=alice) for _ in range(2)]

    def disabled(cred_id):
        return db.fetchone("SELECT disabled FROM credentials WHERE id=?", (cred_id,))["disabled"]

    with patch("app.sources.claude.revoke_remote", new=AsyncMock()):
        sign_in(web, bob)
        refused = web.post(path.format(cred_id=creds[0]), follow_redirects=False)
        assert refused.status_code == 403, refused.text
        assert disabled(creds[0]) == 0
        sign_in(web, alice)
        assert web.post(path.format(cred_id=creds[0]), follow_redirects=False).status_code == 303
        sign_in(web, admin)
        assert web.post(path.format(cred_id=creds[1]), follow_redirects=False).status_code == 303
    assert [disabled(c) for c in creds] == [1, 1]


def test_alerts_memories_and_grants_belong_to_their_owner(web, alice, bob):
    endpoint = "https://push.example/alice-device"
    db.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at) VALUES (?,?,?,?,?,?)",
        (alice, endpoint, "k", "a", "", db.now()),
    )
    memory.save(alice, name="db-policy", type="user", description="alice private memory", body="body")
    memory_id = memory.list_all(alice)[0]["id"]
    thread = chats.create_thread(alice, model="offline")
    pk = chats.resolve_thread_pk(alice, thread["id"])
    permissions.add_grant(alice, pk, 1, "dir", "/srv")
    grant_id = db.fetchone("SELECT id FROM chat_permission_grants WHERE user_id=?", (alice,))["id"]

    sign_in(web, bob)
    gone = web.post("/notifications/unsubscribe", json={"endpoint": endpoint})
    assert gone.status_code == 200 and gone.json()["count"] == 0
    edit = web.put(f"/settings/memories/{memory_id}", json={"type": "user", "description": "pwned", "body": "pwned"})
    assert edit.status_code == 404
    assert web.delete(f"/settings/memories/{memory_id}").status_code == 404
    assert web.delete(f"/chat/permissions/grants/{grant_id}").status_code == 404
    assert "alice private memory" not in web.get("/settings").text

    assert db.fetchone("SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id=?", (alice,))["n"] == 1
    assert memory.list_all(alice)[0]["description"] == "alice private memory"
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_permission_grants WHERE id=?", (grant_id,))["n"] == 1

    sign_in(web, alice)
    assert web.delete(f"/chat/permissions/grants/{grant_id}").status_code == 200
    assert web.delete(f"/settings/memories/{memory_id}").status_code == 200


def test_usage_of_other_accounts_is_for_admins_only(web, alice, bob):
    admin = quick_user("root@core.test", admin=True)
    calls = []

    def analytics(user_id, **kwargs):
        calls.append((user_id, kwargs["all_users"], kwargs["subject_id"], kwargs["include_accounts"]))
        return {}

    with patch("app.stats.usage_analytics", new=analytics):
        sign_in(web, bob)
        for scope in ("all", f"user:{alice}"):
            assert web.get("/usage.json", params={"scope": scope}).status_code == 200
        sign_in(web, admin)
        assert web.get("/usage.json", params={"scope": f"user:{alice}"}).status_code == 200
        assert web.get("/usage.json", params={"scope": "all"}).status_code == 200
    assert calls == [(bob, False, None, False), (bob, False, None, False),
                     (admin, False, alice, True), (admin, True, None, True)]
