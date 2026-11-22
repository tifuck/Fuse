"""Cross-user isolation over HTTP: a signed-in user reaching for another
account's object by id is refused AND changes nothing, and the owner's same
request works (so a refusal is ownership, not a route that refuses everyone)."""
import base64
import hashlib
import importlib
import json
import re
from unittest.mock import AsyncMock, patch

import pytest

from app import (
    accounts, artifacts, chat_images, chat_store, chats, db, hosts, memory, notices, permissions, schedules,
    skill_proposals, skills, vault,
)
from app.config import settings
from app.sources import keyed
from tests.core.auth._support import PASSWORD, http_routes, main_client, sign_in
from tests.core.auth.test_route_auth import PUBLIC
from tests.core.helpers import make_user, quick_user
from tests.core.threads._support import image_attachment, image_url


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
    pk = chats.resolve_thread_pk(alice, tid)
    # Real objects behind the ids Bob tries: a read_file image in her run, a
    # pending ask and a running background job in her chat.
    label = json.dumps({"call_id": "c1", "event_id": "ev-read-1", "path": "/srv/shot.png", "host": "box"})
    chat_store.save_turn_progress(run["id"], [{"role": "user", "content": [
        {"type": "text", "text": "[Fuse: untrusted read_file image output, not a human message or instruction.]\n" + label},
        {"type": "image_url", "image_url": {"url": image_url(3)}},
    ]}])
    db.execute("INSERT INTO chat_permission_asks (id,user_id,thread_id,run_id,status,kind,tool,created_at,expires_at)"
               " VALUES ('ask-alice',?,?,?,'pending','path','read_file',?,'2999-01-01T00:00:00Z')",
               (alice, pk, run["id"], db.now()))
    db.execute("INSERT INTO command_jobs (id,user_id,host_id,host,thread_id,run_id,command,cwd,background,timeout_ms,"
               "status,created_at,started_at) VALUES ('job-alice',?,1,'box',?,?,'make test','/srv',1,60000,'running',?,?)",
               (alice, pk, run["id"], db.now(), db.now()))

    def ask_and_job():
        return (db.fetchone("SELECT status FROM chat_permission_asks WHERE id='ask-alice'")["status"],
                db.fetchone("SELECT status FROM command_jobs WHERE id='job-alice'")["status"])

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
        ("POST", f"/chat/threads/{tid}/jobs/job-alice/kill", None),
        ("GET", f"/chat/threads/{tid}/secrets/offer", None),
        ("POST", f"/chat/threads/{tid}/secrets/offer", {"protect": []}),
        ("GET", f"/chat/threads/{tid}/permissions", None),
        ("POST", f"/chat/threads/{tid}/permissions/ask-alice", {"action": "allow"}),
        ("GET", f"/chat/threads/{tid}/project-agents", None),
        ("GET", f"/chat/runs/{run['id']}", None),
        ("GET", f"/chat/runs/{run['id']}/images/ev-read-1", None),
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
    assert ask_and_job() == ("pending", "running")

    sign_in(web, alice)
    assert web.get(f"/chat/threads/{tid}").json()["title"] == "Alice thread"
    assert web.get(f"/chat/runs/{run['id']}").status_code == 200
    renamed = web.patch(f"/chat/threads/{tid}", json={"title": "Renamed", "expected_revision": rev, "command_id": "a1"})
    assert renamed.status_code == 200, renamed.text
    image = web.get(f"/chat/runs/{run['id']}/images/ev-read-1")
    assert image.status_code == 200 and image.content == base64.b64decode(image_url(3).partition(",")[2])
    assert web.post(f"/chat/threads/{tid}/permissions/ask-alice", json={"action": "allow"}).status_code == 200
    assert web.post(f"/chat/threads/{tid}/jobs/job-alice/kill").status_code == 200
    assert ask_and_job() == ("allowed", "cancelling")


def test_chat_images_notices_and_pin_order_belong_to_their_owner(web, alice, bob, monkeypatch):
    monkeypatch.setattr(chat_images, "_cache", type(chat_images._cache)())
    monkeypatch.setattr(chat_images, "_cache_bytes", 0)
    thread = chats.create_thread(alice, model="offline", title="one")
    chat_store.start_turn(alice, thread["id"], "what is this?", thread["revision"], None, attachments=[image_attachment(5)])
    sha = hashlib.sha256(image_url(5).encode("ascii")).hexdigest()
    pixels = base64.b64decode(image_url(5).partition(",")[2])
    assert chat_images.attachment_image(alice, sha) == ("image/png", pixels)  # her cache is warm
    notices.post(alice, "alice-notice", title="Alice notice", body="private", kind="warn")
    [notice] = notices.list_open(alice)
    other = chats.create_thread(alice, model="offline", title="two")["id"]
    for order, public_id in enumerate((thread["id"], other)):
        db.execute("UPDATE chat_threads SET pinned=1, pin_order=? WHERE id=?",
                   (order, chats.resolve_thread_pk(alice, public_id)))

    def pins():
        return [r["pin_order"] for r in db.fetchall("SELECT pin_order FROM chat_threads WHERE user_id=? ORDER BY id", (alice,))]

    sign_in(web, bob)
    assert web.get(f"/chat/images/{sha}").status_code == 404
    assert web.post(f"/chat/notices/{notice['id']}/dismiss").status_code == 200
    reordered = web.post("/chat/threads/pin-order", json={"ids": [other, thread["id"]], "command_id": "b1"})
    assert reordered.status_code == 200 and reordered.json()["threads"] == []
    assert [n["id"] for n in notices.list_open(alice)] == [notice["id"]]
    assert pins() == [0, 1]

    sign_in(web, alice)
    shown = web.get(f"/chat/images/{sha}")
    assert shown.status_code == 200 and shown.content == pixels
    assert web.post("/chat/threads/pin-order", json={"ids": [other, thread["id"]], "command_id": "a1"}).status_code == 200
    assert pins() == [1, 0]
    assert web.post(f"/chat/notices/{notice['id']}/dismiss").status_code == 200
    assert notices.list_open(alice) == []


def test_project_instructions_cannot_be_written_into_another_accounts_project(web, bob):
    alice = make_user("alice@core.test")
    host, project = _alice_machine(alice)
    tid = chats.create_thread(alice, model="offline")["id"]
    db.execute("UPDATE chat_threads SET kind='project', host_id=?, project_id=? WHERE id=?",
               (host["id"], project["id"], chats.resolve_thread_pk(alice, tid)))
    load = AsyncMock(return_value=("AGENTS.md", "Be kind.\n"))
    write = AsyncMock(return_value=("Wrote AGENTS.md", {"status": "done"}))

    with patch("app.workspace.load_project_agents", new=load), patch("app.workspace.execute", new=write):
        sign_in(web, bob)
        res = web.put(f"/chat/threads/{tid}/project-agents", json={"content": "pwned"})
        assert res.status_code == 404, res.text
        assert write.await_count == 0 and load.await_count == 0
        sign_in(web, alice)
        mine = web.put(f"/chat/threads/{tid}/project-agents", json={"content": "Be kind."})
        assert mine.status_code == 200 and mine.json()["state"] == "loaded", mine.text
    [call] = write.await_args_list
    assert call.args[0] == "write_file" and json.loads(call.args[1]) == {"path": "AGENTS.md", "content": "Be kind.\n"}
    assert (call.kwargs["user_id"], call.kwargs["bound_host_id"], call.kwargs["bound_cwd"]) == (alice, host["id"], "/srv/site")


def test_skills_and_skill_drafts_belong_to_their_owner(web, alice, bob, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_directory", str(tmp_path / "skills"))
    md = "---\nname: alice-notes\ndescription: Use when Alice takes notes.\n---\nAlice's private method.\n"
    skills.install_files(alice, {"SKILL.md": md.encode()}, origin="upload", slug="alice-notes")
    pk = chats.resolve_thread_pk(alice, chats.create_thread(alice, model="offline", tools=[])["id"])
    draft = skill_proposals.create(alice, thread_id=pk, name="alice-draft", description="Takes notes.",
                                   body="Alice's private draft.")["id"]
    digest = db.fetchone("SELECT content_hash FROM user_skills WHERE user_id=? AND slug='alice-notes'", (alice,))["content_hash"]

    def rows():
        return (db.fetchall("SELECT * FROM user_skills ORDER BY user_id, slug"),
                db.fetchall("SELECT * FROM skill_proposals ORDER BY id"))

    before = rows()
    sign_in(web, bob)
    for method, path, body in [
        ("GET", "/skills/alice-notes/review", None),
        ("POST", "/skills/alice-notes/approve", {"hash": digest}),
        ("PATCH", "/skills/alice-notes", {"enabled": True}),
        ("DELETE", "/skills/alice-notes", None),
        ("GET", f"/skills/proposals/{draft}", None),
        ("POST", f"/skills/proposals/{draft}/confirm", {}),
        ("POST", f"/skills/proposals/{draft}/reject", None),
    ]:
        res = web.request(method, path, json=body)
        assert res.status_code == 404, (method, path, res.status_code, res.text[:200])
        assert "private method" not in res.text and "private draft" not in res.text, path
    assert rows() == before

    sign_in(web, alice)
    review = web.get("/skills/alice-notes/review")
    assert review.status_code == 200 and review.json()["hash"] == digest
    assert web.post("/skills/alice-notes/approve", json={"hash": digest}).status_code == 200
    assert db.fetchone("SELECT enabled FROM user_skills WHERE user_id=? AND slug='alice-notes'", (alice,))["enabled"] == 1
    assert "private draft" in web.get(f"/skills/proposals/{draft}").text
    assert web.post(f"/skills/proposals/{draft}/reject").json()["status"] == "rejected"


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
    def artifact_rows():
        # viewed_at moves on purpose: opening a public link keeps it from expiring.
        return [{k: v for k, v in dict(r).items() if k != "viewed_at"} for r in db.fetchall("SELECT * FROM artifacts ORDER BY id")]

    rows_before = artifact_rows()

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
    # A public artifact is viewable by design; it is the shared page only,
    # served sandboxed.
    shown = web.get(f"/a/{public}/content")
    assert shown.status_code == 200 and "shared" in shown.text
    assert "allow-scripts" in _sandbox(shown) and "allow-same-origin" not in _sandbox(shown)
    assert web.get(f"/a/{public}/meta").json()["owner"] is False
    # His own artifact cannot be filed into her chat.
    assert web.post(f"/a/{own}/attach", json={"thread_id": alice_thread}).status_code == 404
    assert "Private" not in web.get("/artifacts/list").text
    assert artifact_rows() == rows_before

    sign_in(web, alice)
    for method, path, body in owner_ops(private, alice_thread)[:-3]:
        res = web.request(method, path, json=body, follow_redirects=False)
        assert res.status_code < 400, (method, path, res.status_code, res.text[:200])


def _sandbox(res) -> list | None:
    """The sandbox flags of a response's CSP; None without a sandbox directive."""
    csp = res.headers.get("content-security-policy", "")
    return next((d.split()[1:] for d in csp.split(";") if d.split()[:1] == ["sandbox"]), None)


def test_artifact_pages_go_out_sandboxed_through_the_whole_app(alice):
    private = artifacts.create(alice, title="Private", html="<p>secret sauce</p>")["identifier"]
    share = artifacts.create(alice, title="Public", html="<p>shared</p>", visibility="public")["share_url"].rsplit("/", 1)[-1]
    client = main_client()

    framed = client.get(f"/a/{share}/content", headers={"sec-fetch-dest": "iframe"})
    assert framed.status_code == 200 and "shared" in framed.text
    assert "allow-scripts" in _sandbox(framed) and "allow-same-origin" not in _sandbox(framed)
    assert "frame-ancestors 'self'" in framed.headers["content-security-policy"]
    assert framed.headers["x-frame-options"] == "SAMEORIGIN"
    # Opened as a page of its own it goes to the viewer, never bare on the origin.
    for query, location in (("", f"/a/{share}"), ("?v=1", f"/a/{share}?v=1")):
        page = client.get(f"/a/{share}/content{query}", headers={"sec-fetch-dest": "document"}, follow_redirects=False)
        assert (page.status_code, page.headers["location"]) == (303, location)
        assert "shared" not in page.text
    for dest in ("iframe", "empty"):
        hidden = client.get(f"/a/{private}/content", headers={"sec-fetch-dest": dest})
        assert hidden.status_code == 404 and "secret sauce" not in hidden.text

    sign_in(client, alice)
    download = client.get(f"/a/{private}/download")
    assert download.status_code == 200 and "secret sauce" in download.text
    assert download.headers["content-security-policy"] == "default-src 'none'; sandbox; frame-ancestors 'none'"
    assert _sandbox(download) == [] and download.headers["x-frame-options"] == "DENY"
    assert download.headers["content-disposition"].startswith("attachment;")


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


_REVOKE = re.compile(r"^/(?:sources|pool)/(\w+|keys/\{pid\}|p/\{pid\})/\{cred_id\}/revoke$")


def _revoke_paths():
    routes, _mounts, _sockets = http_routes()
    return sorted(path for method, path in routes if method == "POST" and _REVOKE.match(path))


def test_every_pool_has_a_revoke_route():
    kinds = {_REVOKE.match(path).group(1) for path in _revoke_paths()}
    assert {"grok", "chatgpt", "claude", "cursor", "devin", "kimi", "mimo", "nanogpt", "openrouter",
            "refuseless", "zai", "explabs", "keys/{pid}", "p/{pid}"} <= kinds


@pytest.mark.parametrize("path", _revoke_paths())
def test_only_the_donor_or_an_admin_can_remove_a_pool_credential(web, path, alice, bob):
    kind = _REVOKE.match(path).group(1)
    # A personal API key is its owner's alone: an admin manages site keys on
    # their own route, so the admin is refused here like anyone else.
    personal = kind == "keys/{pid}"
    pid = {"keys/{pid}": "deepseek", "p/{pid}": "claude"}.get(kind, kind)
    if personal:
        source_id = keyed.ensure_source(pid)
    else:
        source_id = getattr(importlib.import_module(f"app.sources.{pid}"), f"ensure_{pid}_source")()
    admin = quick_user("root@core.test", admin=True)
    creds = [accounts.add_credential(source_id, "alice", "not-a-real-secret", donated_by=alice) for _ in range(2)]

    def disabled(cred_id):
        return db.fetchone("SELECT disabled FROM credentials WHERE id=?", (cred_id,))["disabled"]

    def revoke(cred_id):
        return web.post(path.format(pid=pid, cred_id=cred_id), follow_redirects=False)

    with patch("app.sources.claude.revoke_remote", new=AsyncMock()):
        sign_in(web, bob)
        refused = revoke(creds[0])
        assert refused.status_code == (404 if personal else 403), refused.text
        assert disabled(creds[0]) == 0
        sign_in(web, alice)
        assert revoke(creds[0]).status_code == 303
        sign_in(web, admin)
        assert revoke(creds[1]).status_code == (404 if personal else 303)
    assert [disabled(c) for c in creds] == [1, 0 if personal else 1]


# Every route that takes an account's object id from its URL, by the test
# that shows another account can neither read nor change that object through
# it. A new route with an id fails test_every_route_with_an_id_is_covered
# until it is tested and listed here.
ISOLATED = {
    # test_threads_and_runs_are_private_to_their_owner
    ("GET", "/chat/threads/{thread_id}"), ("PATCH", "/chat/threads/{thread_id}"), ("DELETE", "/chat/threads/{thread_id}"),
    ("POST", "/chat/threads/{thread_id}/turns"), ("POST", "/chat/threads/{thread_id}/queue"),
    ("DELETE", "/chat/threads/{thread_id}/queue/{item_id}"), ("POST", "/chat/threads/{thread_id}/steer"),
    ("POST", "/chat/threads/{thread_id}/title"), ("POST", "/chat/threads/{thread_id}/recap"),
    ("POST", "/chat/threads/{thread_id}/compact"), ("POST", "/chat/threads/{thread_id}/fork"),
    ("POST", "/chat/threads/{thread_id}/project"), ("GET", "/chat/threads/{thread_id}/agents"),
    ("GET", "/chat/threads/{thread_id}/jobs"), ("POST", "/chat/threads/{thread_id}/jobs/{job_id}/kill"),
    ("GET", "/chat/threads/{thread_id}/secrets/offer"), ("POST", "/chat/threads/{thread_id}/secrets/offer"),
    ("GET", "/chat/threads/{thread_id}/permissions"), ("POST", "/chat/threads/{thread_id}/permissions/{ask_id}"),
    ("GET", "/chat/threads/{thread_id}/project-agents"), ("GET", "/chat/runs/{run_id}"),
    ("GET", "/chat/runs/{run_id}/images/{event_id}"), ("POST", "/chat/runs/{run_id}/cancel"),
    ("POST", "/chat/runs/{run_id}/resume"), ("POST", "/chat/runs/{run_id}/message"),
    # test_chat_images_notices_and_pin_order_belong_to_their_owner
    ("GET", "/chat/images/{sha}"), ("POST", "/chat/notices/{notice_id}/dismiss"),
    # test_project_instructions_cannot_be_written_into_another_accounts_project
    ("PUT", "/chat/threads/{thread_id}/project-agents"),
    # test_hosts_and_projects_are_private_to_their_owner
    ("PATCH", "/hosts/{host_id}"), ("DELETE", "/hosts/{host_id}"), ("POST", "/hosts/{host_id}/projects"),
    ("PATCH", "/hosts/{host_id}/projects/{project_id}"), ("DELETE", "/hosts/{host_id}/projects/{project_id}"),
    ("PUT", "/hosts/{host_id}/projects/{project_id}/logo"), ("DELETE", "/hosts/{host_id}/projects/{project_id}/logo"),
    ("GET", "/projects/{project_id}/logo"), ("POST", "/hosts/{host_id}/probe"), ("POST", "/hosts/{host_id}/test"),
    ("POST", "/hosts/{host_id}/trust"), ("POST", "/hosts/{host_id}/browse"), ("POST", "/hosts/{host_id}/mkdir"),
    ("DELETE", "/hosts/pairings/{pairing_id}"),
    # test_artifacts_are_private_to_their_owner
    ("GET", "/a/{public_id}/source"), ("GET", "/a/{public_id}/diff"), ("GET", "/a/{public_id}/download"),
    ("POST", "/a/{public_id}/visibility"), ("POST", "/a/{public_id}/title"), ("POST", "/a/{public_id}/pin"),
    ("POST", "/a/{public_id}/rotate-link"), ("POST", "/a/{public_id}/revert"), ("POST", "/a/{public_id}/attach"),
    ("POST", "/a/{public_id}/continue"), ("POST", "/a/{public_id}/archive"), ("POST", "/a/{public_id}/restore"),
    ("POST", "/a/{public_id}/delete"),
    # test_schedules_are_private_to_their_owner
    ("GET", "/schedules/{token}"), ("PATCH", "/schedules/{token}"), ("DELETE", "/schedules/{token}"),
    ("POST", "/schedules/{token}/run"), ("GET", "/schedules/{token}/fires"),
    # test_api_keys_cannot_be_revoked_or_seen_by_another_account
    ("POST", "/keys/{key_id}/delete"),
    # test_alerts_memories_and_grants_belong_to_their_owner
    ("PUT", "/settings/memories/{memory_id}"), ("DELETE", "/settings/memories/{memory_id}"),
    ("DELETE", "/chat/permissions/grants/{grant_id}"),
    # test_skills_and_skill_drafts_belong_to_their_owner
    ("GET", "/skills/{slug}/review"), ("POST", "/skills/{slug}/approve"), ("PATCH", "/skills/{slug}"),
    ("DELETE", "/skills/{slug}"), ("GET", "/skills/proposals/{proposal_id}"),
    ("POST", "/skills/proposals/{proposal_id}/confirm"), ("POST", "/skills/proposals/{proposal_id}/reject"),
    # Elsewhere: features/test_backdrops.py::test_only_the_owner_reads_or_deletes_and_the_document_is_sandboxed
    ("DELETE", "/api/backdrops/{public_id}"), ("GET", "/backdrop/{public_id}"),
    # secrets/test_secret_http.py::test_settings_never_return_a_value
    ("POST", "/settings/secrets/{secret_id}/reveal"), ("DELETE", "/settings/secrets/{secret_id}"),
    # agents/test_pairing_http.py::test_the_stats_socket_opens_only_for_the_users_own_enabled_connector
    ("WS", "/hosts/{host_id}/stats"),
}
# The id names a provider or pool kind, not an account's object.
NOT_AN_OBJECT = {("POST", "/sources/keys/{pid}/add"), ("POST", "/sources/models/{provider}"),
                 ("POST", "/sources/pool/{kind}/prefs")}


def test_every_route_with_an_id_is_covered():
    routes, _mounts, sockets = http_routes()
    with_ids = {(method, path) for method, path in routes
                if "{" in path and (method, path) not in PUBLIC and not path.startswith("/admin")}
    with_ids |= {("WS", path) for path in sockets if "{" in path}
    covered = ISOLATED | NOT_AN_OBJECT | {("POST", path) for path in _revoke_paths()}
    assert not with_ids - covered, sorted(with_ids - covered)
    assert not covered - with_ids, sorted(covered - with_ids)


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
