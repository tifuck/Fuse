"""Artifacts: versions and base_version conflicts, exact edits, pruning that
keeps the shared version, sharing links (owner-only identifiers, fresh links
on every share), archive, owner isolation, limits, the chat/project scope,
and the account switch frozen into each run."""
import asyncio
import json
import threading
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app import artifacts, chat_store, chats, db, permissions
from app.artifacts import ArtifactError
from app.chat_tools import execute_tool
from tests.core.helpers import fake_provider, quick_user, run_async, run_turn, text_frames, tool_call_frames, wait_until

from ._support import make_host, make_project, waiting_on

PAGE = "<!doctype html><html><body>{}</body></html>"


def make(uid, body="v1", **kw):
    return artifacts.create(uid, title=kw.pop("title", "Page"), html=PAGE.format(body), **kw)


def html_at(uid, ident, version=None):
    return artifacts.view(artifacts.get_owned(uid, ident), include_html=True, version=version)["html"]


def link(art):
    return art["share_url"].rsplit("/", 1)[-1]


def test_framed_pages_may_connect_only_to_the_csp_hosts():
    headers = artifacts.content_headers(framable=True)
    allow = headers["Connection-Allowlist"]
    assert allow.startswith("(") and allow.endswith("); redirects=allow") and "webrtc=allow" not in allow
    assert "response-origin" not in allow and "webrtc 'block'" in headers["Content-Security-Policy"]
    for host in ("https://cdn.jsdelivr.net", "https://unpkg.com", "https://fonts.gstatic.com"):
        assert f'"{host}/*"' in allow


def test_create_is_private_v1_and_updates_add_versions(fresh_db):
    uid = quick_user()
    art = make(uid, title="  Sales   report ")
    assert (art["title"], art["visibility"], art["version"], art["versions"]) == ("Sales report", "private", 1, [1])
    two = artifacts.update(uid, art["identifier"], html=PAGE.format("v2"), note="second")
    assert (two["version"], two["versions"]) == (2, [1, 2])
    assert "v1" in html_at(uid, art["identifier"], 1) and "v2" in html_at(uid, art["identifier"])
    # A metadata change is not a new version.
    renamed = artifacts.set_title(uid, art["identifier"], "Renamed")
    assert (renamed["title"], renamed["version"]) == ("Renamed", 2)


def test_a_stale_base_version_is_refused_without_a_new_version(fresh_db):
    uid = quick_user()
    ident = make(uid)["identifier"]
    artifacts.update(uid, ident, html=PAGE.format("v2"), base_version=1)
    with pytest.raises(ArtifactError) as stale:
        artifacts.update(uid, ident, html=PAGE.format("lost"), base_version=1)
    assert stale.value.status_code == 409
    with pytest.raises(ArtifactError) as stale_edit:
        artifacts.edit(uid, ident, {"old_string": "v2", "new_string": "v3"}, base_version=1)
    assert stale_edit.value.status_code == 409
    assert artifacts.view(artifacts.get_owned(uid, ident))["versions"] == [1, 2]


def test_edits_are_exact_replacements_published_as_one_version(fresh_db):
    uid = quick_user()
    ident = artifacts.create(uid, title="T", html="<p>a</p><p>a</p><h1>t</h1>")["identifier"]
    for edits in ({"old_string": "zzz", "new_string": "y"},           # not found
                  {"old_string": "<p>a</p>", "new_string": "<p>b</p>"},  # ambiguous
                  {"old_string": "t", "new_string": "t"},               # no-op
                  [], [{"old_string": "", "new_string": "x"}]):
        with pytest.raises(ArtifactError):
            artifacts.edit(uid, ident, edits)
    done = artifacts.edit(uid, ident, [{"old_string": "<p>a</p>", "new_string": "<p>b</p>", "replace_all": True},
                                       {"old_string": "<h1>t</h1>", "new_string": "<h1>T</h1>"}], note="fix")
    assert done["version"] == 2
    assert html_at(uid, ident) == "<p>b</p><p>b</p><h1>T</h1>"
    # A failing edit in a batch applies none of it.
    with pytest.raises(ArtifactError):
        artifacts.edit(uid, ident, [{"old_string": "<h1>T</h1>", "new_string": "<h1>X</h1>"},
                                    {"old_string": "missing", "new_string": "y"}])
    assert artifacts.get_owned(uid, ident)["current_version"] == 2


def test_pruning_keeps_the_shared_version_and_revert_republishes(fresh_db):
    uid = quick_user()
    ident = make(uid)["identifier"]
    artifacts.set_shared_version(uid, ident, 1)
    with patch.object(artifacts, "WRITES_PER_MIN", 1000):  # many writes on purpose
        for n in range(2, artifacts.MAX_VERSIONS + 4):
            artifacts.update(uid, ident, html=PAGE.format(f"v{n}"))
    versions = artifacts.view(artifacts.get_owned(uid, ident))["versions"]
    assert 1 in versions and len(versions) == artifacts.MAX_VERSIONS
    assert 2 not in versions  # the oldest unpinned version went first
    shared = artifacts.set_visibility(uid, ident, "public")
    viewer = artifacts.client_payload(artifacts.get_for_view(link(shared), None), owner=False, include_html=True)
    assert (viewer["versions"], viewer["version"]) == ([1], 1) and "v1" in viewer["html"]
    back = artifacts.revert(uid, ident, 1)
    assert back["version"] == artifacts.MAX_VERSIONS + 4
    assert "v1" in html_at(uid, ident) and back["history"][-1]["note"] == "Restored v1"
    with pytest.raises(ArtifactError):
        artifacts.set_shared_version(uid, ident, 2)  # pruned


def test_sharing_mints_a_fresh_link_and_the_identifier_never_becomes_public(fresh_db):
    owner, other = quick_user(), quick_user("other@core.test")
    ident = make(owner)["identifier"]
    assert artifacts.get_for_view(ident, None) is None and artifacts.get_for_view(ident, other) is None
    first = link(artifacts.set_visibility(owner, ident, "public"))
    assert first != ident
    assert artifacts.get_for_view(ident, None) is None
    row, is_owner = artifacts.resolve(first, other)
    assert row is not None and is_owner is False
    assert link(artifacts.set_visibility(owner, ident, "public")) == first  # already public: same link
    artifacts.set_visibility(owner, ident, "private")
    assert artifacts.get_for_view(first, None) is None
    second = link(artifacts.set_visibility(owner, ident, "public"))
    assert second != first and artifacts.get_for_view(first, other) is None
    assert artifacts.get_for_view(second, None) is not None
    third = link(artifacts.rotate_share(owner, ident))
    assert third != second and artifacts.get_for_view(second, None) is None
    assert artifacts.get_for_view(ident, owner) is not None  # the owner's references keep working


def test_other_accounts_cannot_touch_an_artifact(fresh_db):
    owner, other = quick_user(), quick_user("other@core.test")
    ident = make(owner, visibility="public")["identifier"]
    for attempt in (lambda: artifacts.update(other, ident, title="Stolen"),
                    lambda: artifacts.edit(other, ident, {"old_string": "v1", "new_string": "x"}),
                    lambda: artifacts.set_visibility(other, ident, "private"),
                    lambda: artifacts.set_archived(other, ident, True),
                    lambda: artifacts.rotate_share(other, ident),
                    lambda: artifacts.continue_in_chat(other, ident),
                    lambda: artifacts.read_source(other, ident)):
        with pytest.raises(ArtifactError) as refused:
            attempt()
        assert refused.value.status_code == 404
    assert artifacts.delete(other, ident) is False
    assert artifacts.list_for_user(other) == []
    text, trace = artifacts.execute_from_tool("update_artifact", {"identifier": ident, "html": "<p>x</p>"},
                                              user_id=other, thread_id=None)
    assert trace["status"] == "error"
    row = artifacts.get_owned(owner, ident)
    assert (row["title"], row["current_version"], row["visibility"]) == ("Page", 1, "public")


def test_archive_hides_an_artifact_from_everyone_until_restored(fresh_db):
    owner, other = quick_user(), quick_user("other@core.test")
    shared = make(owner, visibility="public")
    ident = shared["identifier"]
    thread = chats.create_thread(owner, model="offline")
    artifacts.attach_thread(owner, ident, chats.resolve_thread_pk(owner, thread["id"]))
    artifacts.set_archived(owner, ident, True)
    for viewer in (owner, other, None):
        assert artifacts.get_for_view(ident, viewer) is None
        assert artifacts.get_for_view(link(shared), viewer) is None
    assert artifacts.list_for_prompt(owner, thread["id"]) == []
    for attempt in (lambda: artifacts.update(owner, ident, title="Nope"),
                    lambda: artifacts.continue_in_chat(owner, ident)):
        with pytest.raises(ArtifactError) as refused:
            attempt()
        assert refused.value.status_code == 409
    artifacts.set_archived(owner, ident, False)
    assert artifacts.get_for_view(ident, owner) is not None
    assert [a["identifier"] for a in artifacts.list_for_prompt(owner, thread["id"])] == [ident]
    assert artifacts.delete(owner, ident) is True
    assert artifacts.get_by_public_id(ident) is None


def test_limits_refuse_oversized_placeholder_and_excess_artifacts(fresh_db):
    uid = quick_user()
    with pytest.raises(ArtifactError) as big:
        artifacts.create(uid, title="big", html="x" * (artifacts.HTML_MAX + 1))
    assert big.value.status_code == 413
    for bad in ("   ", "[stored, 1200 chars]", "[redacted]"):
        with pytest.raises(ArtifactError):
            artifacts.create(uid, title="t", html=bad)
    with pytest.raises(ArtifactError):
        artifacts.create(uid, title="t", html="<p/>", fmt="pdf")
    with patch.object(artifacts, "MAX_PER_USER", 2):
        make(uid)
        make(uid)
        with pytest.raises(ArtifactError) as full:
            make(uid)
        assert full.value.status_code == 413
    assert len(artifacts.list_for_user(uid)) == 2


def test_writes_are_rate_limited_per_account(fresh_db):
    uid, other = quick_user(), quick_user("other@core.test")
    with patch.object(artifacts, "WRITES_PER_MIN", 3):
        ident = make(uid)["identifier"]
        artifacts.set_title(uid, ident, "a")
        artifacts.set_title(uid, ident, "b")
        with pytest.raises(ArtifactError) as limited:
            artifacts.delete(uid, ident)
        assert limited.value.status_code == 429
        make(other)  # another account has its own window
    assert artifacts.get_owned(uid, ident) is not None


def test_a_visibility_change_survives_a_concurrent_page_update(fresh_db):
    uid = quick_user()
    ident = make(uid)["identifier"]
    real_tx = db.tx
    worker = []

    @contextmanager
    def tx():
        with real_tx() as conn:
            if worker or threading.current_thread() is not threading.main_thread():
                yield conn
                return

            class Interleave:
                def __getattr__(_, name):
                    return getattr(conn, name)

                def execute(_, sql, args=()):
                    cur = conn.execute(sql, args)
                    if sql.lstrip().startswith("SELECT title, visibility") and not worker:
                        # The model's page update arrives between this read and
                        # the write; with the row lock it waits for the commit.
                        racer = threading.Thread(target=artifacts.update, args=(uid, ident),
                                                 kwargs={"html": PAGE.format("v2")})
                        worker.append(racer)
                        racer.start()
                        wait_until(lambda: waiting_on("artifacts") or not racer.is_alive(), timeout=10)
                    return cur

            yield Interleave()

    with patch.object(artifacts.db, "tx", tx):
        artifacts.set_visibility(uid, ident, "public")
    worker[0].join(10)
    row = artifacts.get_owned(uid, ident)
    assert (row["visibility"], int(row["current_version"])) == ("public", 2)


def test_a_chat_lists_only_its_own_artifacts_even_in_a_project(fresh_db):
    uid, other = quick_user(), quick_user("other@core.test")
    project = make_project(uid, make_host(uid))
    a, b, fresh, plain = (chats.create_thread(uid, model="offline", title=t)["id"] for t in ("a", "b", "fresh", "plain"))
    pk = {t: chats.resolve_thread_pk(uid, t) for t in (a, b, fresh, plain)}
    db.execute("UPDATE chat_threads SET project_id=? WHERE id IN (?,?,?)", (project, pk[a], pk[b], pk[fresh]))
    first, second, solo, loose = (make(uid, title=t, thread_id=pk.get(th))["identifier"]
                                  for t, th in (("first", a), ("second", b), ("solo", plain), ("loose", None)))

    def ids(user=uid, **kw):
        return [item["identifier"] for item in artifacts.list_in_scope(user, **kw)]

    assert ids(thread_id=a) == [first]
    assert ids(thread_id=b) == [second]
    assert ids(thread_id=fresh) == []
    assert ids(thread_id=plain) == [solo]
    assert ids() == []
    assert loose not in ids(thread_id=a) + ids(thread_id=plain)
    assert ids(other, thread_id=a) == []


def test_unopened_artifacts_expire_to_a_stub_that_goes_with_its_chats(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", title="t")["id"]
    pk = chats.resolve_thread_pk(uid, thread)
    stale, opened, recent = (make(uid, title=t, thread_id=pk) for t in ("stale", "opened", "recent"))
    old = "2026-01-01T00:00:00Z"
    db.execute("UPDATE artifacts SET created_at=?, updated_at=? WHERE id IN (?,?)", (old, old, stale["id"], opened["id"]))
    artifacts.touch(artifacts.get_owned(uid, opened["identifier"]))

    assert artifacts.expire_idle() == {"expired": 1, "stubs_removed": 0}
    assert [i["identifier"] for i in artifacts.list_in_scope(uid, thread_id=thread)] == [opened["identifier"], recent["identifier"]]
    with pytest.raises(ArtifactError) as err:
        artifacts.read_source(uid, stale["identifier"])
    assert err.value.status_code == 410 and "without being opened" in err.value.detail
    assert db.fetchone("SELECT COUNT(*) AS n FROM artifact_versions WHERE artifact_id=?", (stale["id"],))["n"] == 0
    # Deleting the chat drops its links (a cascade on the real schema): the stub goes too.
    db.execute("DELETE FROM artifact_threads WHERE thread_id=?", (pk,))
    assert artifacts.expire_idle() == {"expired": 0, "stubs_removed": 1}
    assert artifacts.get_owned(uid, stale["identifier"]) is None


def test_a_model_cannot_publish_and_needs_an_answer_to_change_a_shared_page(fresh_db):
    uid = quick_user()

    async def body():
        text, trace = await execute_tool("create_artifact", json.dumps({"title": "t", "html": "<p>x</p>", "visibility": "public"}),
                                         user_id=uid, allowed_urls=set())
        made = trace["identifier"]
        assert trace["visibility"] == "private" and "Sharing is the user's call" in text
        shared = artifacts.create(uid, title="Shared", html="<p>a</p>", visibility="public")["identifier"]
        gate = permissions.Gate(user_id=uid, thread_id=None, run_id="r", requester=None,
                                unattended=True, notify=lambda event: None, cancel=None)
        for name, args in (("update_artifact", {"identifier": shared, "html": "<p>b</p>"}),
                           ("create_artifact", {"id": shared, "title": "Shared", "html": "<p>c</p>"})):
            _, trace = await execute_tool(name, json.dumps(args), user_id=uid, allowed_urls=set(), gate=gate)
            assert trace.get("kind") == "permission_denied", name
        _, private = await execute_tool("update_artifact", json.dumps({"identifier": made, "html": "<p>y</p>"}),
                                        user_id=uid, allowed_urls=set(), gate=gate)
        _, read = await execute_tool("read_artifact", json.dumps({"identifier": shared}),
                                     user_id=uid, allowed_urls=set(), gate=gate)
        return shared, private, read

    shared, private, read = run_async(body())
    assert artifacts.get_owned(uid, shared)["current_version"] == 1
    assert private["status"] == "done" and read["status"] == "done"


def test_the_artifacts_switch_is_frozen_into_the_run_and_refuses_when_off(fresh_db):
    uid = quick_user()
    for on in (True, False):
        chats.set_user_context(uid, artifacts=on)
        made = chats.create_thread(uid, model="offline", tools=[])
        run = chat_store.start_turn(uid, made["id"], "Hi", made["revision"], None)["run"]
        assert run["request"]["artifacts"] is on
        chat_store.finish_run(run["id"], {"content": "x"}, "completed")
    _, trace = asyncio.run(execute_tool("create_artifact", json.dumps({"title": "t", "html": "<p>x</p>"}),
                                        user_id=uid, allowed_urls=set(), session={"artifacts": False}))
    assert trace["kind"] == "disabled"
    assert artifacts.list_for_user(uid) == []


def test_a_real_turn_creates_an_artifact_linked_to_its_chat(fresh_db):
    uid = quick_user()
    call = ("a1", "create_artifact", {"title": "Chart", "html": "<p>chart</p>"})
    with fake_provider(tool_call_frames([call]), text_frames("Made it.")) as provider:
        thread_id, run = run_async(run_turn(uid, "Make me a chart page"))
    assert run["status"] == "completed"
    assert "create_artifact" in [t["function"]["name"] for t in provider.bodies[0]["tools"]]
    [made] = artifacts.list_in_scope(uid, thread_id=thread_id)
    assert (made["title"], made["visibility"]) == ("Chart", "private")
    # Switched off: the next turn's model has no artifact tools at all.
    chats.set_user_context(uid, artifacts=False)
    with fake_provider(text_frames("ok")) as provider:
        run_async(run_turn(uid, "again", thread=chats.get_thread(uid, thread_id)))
    assert not any("artifact" in t["function"]["name"] for t in provider.bodies[0].get("tools") or [])


def test_inline_display_is_stored_traced_and_switchable(fresh_db):
    uid = quick_user()
    spec = {"type": "line", "x": "day", "y": "n", "data": [{"day": "2026-11-01", "n": 3}]}
    text, made = artifacts.execute_from_tool("create_artifact", {"title": "Logins", "display": "inline", "chart": spec},
                                             user_id=uid, thread_id=None)
    assert (made["status"], made["display"], made["format"]) == ("done", "inline", "chart")
    assert "in full inside your reply" in text
    ident = made["identifier"]
    _, edited = artifacts.execute_from_tool("edit_artifact", {"identifier": ident, "edits": [{"old_string": '"n": 3', "new_string": '"n": 4'}]},
                                            user_id=uid, thread_id=None)
    assert (edited["display"], edited["version"]) == ("inline", 2)
    _, moved = artifacts.execute_from_tool("update_artifact", {"identifier": ident, "display": "panel"}, user_id=uid, thread_id=None)
    assert (moved["display"], moved["version"]) == ("panel", 2)
    _, bad = artifacts.execute_from_tool("create_artifact", {"title": "x", "html": PAGE.format(1), "display": "sidebar"},
                                         user_id=uid, thread_id=None)
    assert bad["status"] == "error" and "display" in bad["error"]
    _, chart = artifacts.execute_from_tool("create_artifact", {"title": "c", "chart": spec}, user_id=uid, thread_id=None)
    _, page = artifacts.execute_from_tool("create_artifact", {"title": "p", "html": PAGE.format(1)}, user_id=uid, thread_id=None)
    assert (chart["display"], page["display"]) == ("inline", "panel")


def test_chart_specs_are_checked_before_they_are_stored(fresh_db):
    uid = quick_user()
    for spec, problem in (({"type": "pie", "data": [{"a": 1}]}, "chart type"), ({"type": "bar", "data": []}, "non-empty"),
                          ({"type": "bar", "data": [1, 2]}, "objects"), ("not json", "JSON object")):
        with pytest.raises(ArtifactError, match=problem):
            artifacts.create(uid, title="c", html=spec if isinstance(spec, str) else json.dumps(spec), fmt="chart")
    assert artifacts.list_for_user(uid) == []


def test_charts_written_by_columns_are_stored_as_rows():
    columns = {"type": "bar", "x": ["Mon", "Tue"], "y": [{"label": "Signups", "values": [12, 18]}]}
    chartjs = {"chart": {"type": "line", "labels": ["Mon", "Tue"], "datasets": [{"label": "Signups", "data": [12, 18]}]}}
    for spec in (columns, chartjs):
        stored = json.loads(artifacts._chart_text(json.dumps(spec)))
        assert (stored["x"], stored["y"]) == ("label", ["Signups"])
        assert stored["data"] == [{"label": "Mon", "Signups": 12}, {"label": "Tue", "Signups": 18}]


def test_inline_pages_are_transparent_sized_and_in_the_chat_font():
    page = artifacts.render_document(PAGE.format("x"), inline=True, font="mono|Red Hat Mono|wght@400..700", scale="115")
    assert "background:transparent!important" in page and 'type:"size"' in page
    assert "family=Red+Hat+Mono:wght@400..700" in page and '--fuse-font:"Red Hat Mono"' in page and "--fuse-scale:1.15" in page
    hostile = artifacts.render_document(PAGE.format("x"), inline=True, font='mono|x" onload="|wght@400', scale="9999")
    assert "onload" not in hostile and "fonts.googleapis.com/css2" not in hostile and "--fuse-scale:1.6" in hostile
    assert 'type:"size"' not in artifacts.render_document(PAGE.format("x"))
    chart = artifacts.render_document(artifacts._chart_text(json.dumps({"type": "bar", "data": [{"a": "</script><b>"}]})), "chart")
    assert "window.FUSE_CHART=" in chart and "</script><b>" not in chart
