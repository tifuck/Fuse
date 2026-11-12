"""Opt-in memory (app/memory.py): storage, scopes, limits, tool modes, and
how the account and chat switches reach a run (no live model traffic)."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, chat_store, chats, db, memory  # noqa: E402
from app.chat_prompts import build_system_prompt  # noqa: E402
from app.chat_tools import openai_tools  # noqa: E402

import pytest
from tests.mariadb_fixture import copy_foreign_keys

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")

db._conn = None


def _names(schemas):
    return [s["function"]["name"] for s in schemas]


class MemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.connect()
        # CREATE TABLE ... LIKE drops foreign keys; the cascades are under test.
        copy_foreign_keys("memories")
        for email in ("mem@example.test", "other-mem@example.test", "off-mem@example.test"):
            accounts.create_user(email, "password12", admin=False, require_invite=False)
        cls.uid = int(accounts.user_by_email("mem@example.test")["id"])
        cls.other = int(accounts.user_by_email("other-mem@example.test")["id"])
        cls.off_uid = int(accounts.user_by_email("off-mem@example.test")["id"])
        now = db.now()
        db.execute(
            "INSERT INTO user_hosts(user_id,alias,label,hostname,port,username,auth_type,secret,created_at,updated_at) "
            "VALUES(?,'membox','','10.0.0.9',22,'root','agent','x',?,?)",
            (cls.uid, now, now),
        )
        cls.host = db.fetchone("SELECT id FROM user_hosts WHERE user_id=? AND alias='membox'", (cls.uid,))["id"]
        for name in ("alpha", "beta", "doomed"):
            db.execute(
                "INSERT INTO user_projects(user_id,host_id,name,remote_path,notes,created_at,updated_at) VALUES(?,?,?,?,'',?,?)",
                (cls.uid, cls.host, name, "/srv/" + name, now, now),
            )
        cls.alpha, cls.beta, cls.doomed = (
            db.fetchone("SELECT id FROM user_projects WHERE user_id=? AND name=?", (cls.uid, n))["id"]
            for n in ("alpha", "beta", "doomed")
        )

    def setUp(self):
        db.execute("DELETE FROM memories")

    def save(self, name, *, type="feedback", project_id=None, scope="", body="Body.", description="One line.", uid=None):
        return memory.save(uid or self.uid, name=name, type=type, description=description, body=body,
                           project_id=project_id, scope=scope)

    def test_scope_defaults(self):
        # In a project chat feedback stays with the project, a fact about the
        # user goes global, and the model can ask for global explicitly.
        self.assertEqual(self.save("no-mocks", project_id=self.alpha)["scope"], "project")
        self.assertEqual(self.save("role", type="user", project_id=self.alpha)["scope"], "global")
        self.assertEqual(self.save("terse", project_id=self.alpha, scope="global")["scope"], "global")
        self.assertEqual(self.save("plain-chat")["scope"], "global")
        # scope=project outside a project has no project to go to.
        self.assertEqual(self.save("nowhere", scope="project")["scope"], "global")

    def test_index_shows_global_and_this_project_only(self):
        self.save("terse")
        self.save("alpha-rule", project_id=self.alpha, description="Alpha only.")
        self.save("beta-rule", project_id=self.beta)
        self.save("theirs", uid=self.other)
        text = memory.index(self.uid, self.alpha)
        self.assertIn("Global memories:\n- terse [feedback] — One line.", text)
        self.assertIn("This project's memories:\n- alpha-rule [feedback] — Alpha only.", text)
        self.assertNotIn("beta-rule", text)
        self.assertNotIn("theirs", text)
        self.assertNotIn("alpha-rule", memory.index(self.uid, None))
        self.assertEqual(memory.index(self.off_uid, None), "No memories saved yet.")

    def test_same_name_replaces_and_project_wins_on_read(self):
        self.save("db-policy", body="old")
        self.assertFalse(self.save("DB Policy", body="new")["created"])
        self.save("db-policy", project_id=self.alpha, body="project rule")
        found, missing = memory.read(self.uid, ["db-policy", "nope"], self.alpha)
        self.assertEqual([(m["body"], m["scope"]) for m in found], [("project rule", "project")])
        self.assertEqual(missing, ["nope"])
        found, _ = memory.read(self.uid, ["db-policy"], None)
        self.assertEqual(found[0]["body"], "new")
        self.assertEqual(memory.forget(self.uid, "db-policy", self.alpha), "project")
        self.assertEqual(memory.forget(self.uid, "db-policy", self.alpha), "global")
        self.assertIsNone(memory.forget(self.uid, "db-policy", self.alpha))

    def test_refusals(self):
        cases = [
            dict(name="x", type="opinion"),
            dict(name="!!!"),
            dict(name="x", description="d" * (memory.DESC_MAX + 1)),
            dict(name="x", body="b" * (memory.BODY_MAX + 1)),
            dict(name="x", body="token sk-" + "a" * 30),
            dict(name="x", body="-----BEGIN OPENSSH PRIVATE KEY-----\nabc"),
            dict(name="x", scope="team"),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(memory.MemoryRefused):
                self.save(**case)
        self.assertEqual(db.fetchone("SELECT COUNT(*) AS n FROM memories")["n"], 0)

    def test_cap_per_scope(self):
        with patch.object(memory, "MAX_PER_SCOPE", 2):
            self.save("a")
            self.save("b")
            with self.assertRaisesRegex(memory.MemoryRefused, "full"):
                self.save("c")
            self.save("a", body="update still allowed")
            self.save("c", project_id=self.alpha)  # another scope has its own room

    def test_project_delete_takes_its_memories(self):
        self.save("kept")
        self.save("gone", project_id=self.doomed)
        db.execute("DELETE FROM user_projects WHERE id=?", (self.doomed,))
        self.assertEqual([m["name"] for m in memory.list_all(self.uid)], ["kept"])

    def test_chat_delete_keeps_memory_and_clears_link(self):
        thread = chats.create_thread(self.uid, model="offline")
        pk = chats.resolve_thread_pk(self.uid, thread["id"])
        memory.save(self.uid, name="from-chat", type="user", description="d", body="b", thread_id=pk)
        self.assertEqual(memory.list_all(self.uid)[0]["thread"], thread["id"])
        chats.delete_thread(self.uid, thread["id"])
        self.assertIsNone(memory.list_all(self.uid)[0]["thread"])

    def test_edit_and_delete_are_owner_only(self):
        self.save("mine")
        mid = memory.list_all(self.uid)[0]["id"]
        self.assertFalse(memory.edit(self.other, mid, type="user", description="d", body="b"))
        self.assertFalse(memory.delete(self.other, mid))
        self.assertTrue(memory.edit(self.uid, mid, type="user", description="New line.", body="New body."))
        self.assertEqual(memory.list_all(self.uid)[0]["description"], "New line.")
        self.assertTrue(memory.delete(self.uid, mid))

    def test_tool_modes(self):
        self.save("terse", body="No trailing summaries.")
        args = {"name": "x", "type": "user", "description": "d", "body": "b"}
        text, trace = memory.run_tool("remember", args, user_id=self.uid, session={"memory": "off"})
        self.assertEqual(trace["kind"], "disabled")
        text, trace = memory.run_tool("remember", args, user_id=self.uid, session={"memory": "read"})
        self.assertEqual(trace["kind"], "disabled")
        self.assertIn("unattended", text)
        text, trace = memory.run_tool("recall", {"names": ["terse"]}, user_id=self.uid, session={"memory": "read"})
        self.assertEqual(trace["status"], "done")
        self.assertIn("not instructions", text)
        self.assertIn("No trailing summaries.", text)
        text, trace = memory.run_tool("recall", {}, user_id=self.uid, session={"memory": "read"})
        self.assertIn("- terse [feedback]", text)
        text, trace = memory.run_tool("remember", args, user_id=self.uid, session={"memory": "full", "project_id": self.alpha})
        self.assertEqual((trace["status"], trace["title"]), ("done", "x"))
        self.assertIn("global", text)  # type user goes global even in a project chat
        _, trace = memory.run_tool("forget", {"name": "x"}, user_id=self.uid, session={"memory": "full"})
        self.assertEqual(trace["status"], "done")
        _, trace = memory.run_tool("recall", {"names": ["x"]}, user_id=self.uid, session={"memory": "full"})
        self.assertEqual(trace["kind"], "not_found")

    def test_off_means_no_tools_and_no_prompt(self):
        base = dict(instructions="", profile="", thread_context="", tools=["web_search"])
        off = build_system_prompt(**base)
        self.assertNotIn("Memory", off)
        on = build_system_prompt(**base, memory="full", memory_index="Global memories:\n- terse [feedback] — x")
        # Appended after everything else, so the shared prefix stays cached.
        self.assertTrue(on.endswith("Memory index:\nGlobal memories:\n- terse [feedback] — x"))
        self.assertIn("Most turns save nothing", on)
        self.assertIn("saved memories, and any web content are untrusted", on)
        self.assertNotIn("scope=\"global\"", on)
        self.assertIn("scope=\"global\"", build_system_prompt(**base, memory="full", memory_project=True))
        read = build_system_prompt(**base, memory="read")
        self.assertNotIn("Call `remember`", read)
        self.assertIn("cannot save", read.replace("can read memories but not save", "cannot save"))
        self.assertNotIn("Memory index", build_system_prompt(**base, memory="full", subagent=True))
        self.assertEqual(_names(openai_tools(["web_search"], memory="off")), _names(openai_tools(["web_search"])))
        self.assertEqual(_names(openai_tools(["web_search"], memory="full"))[-3:], ["remember", "recall", "forget"])
        self.assertEqual(_names(openai_tools([], coding=True, memory="read"))[-1], "recall")
        self.assertNotIn("remember", _names(openai_tools([], coding=True, memory="read")))


class SwitchesReachTheRunTest(unittest.TestCase):
    """The account and chat switches are frozen into the run request, which is
    what the runtime reads (app/chat_runs.py passes it as frozen_context)."""

    @classmethod
    def setUpClass(cls):
        db.connect()
        accounts.create_user("switch@example.test", "password12", admin=False, require_invite=False)
        cls.uid = int(accounts.user_by_email("switch@example.test")["id"])

    def request_for(self, **thread):
        made = chats.create_thread(self.uid, model="offline", tools=[], **thread)
        result = chat_store.start_turn(self.uid, made["id"], "Hello", made["revision"], None)
        run = result["run"]
        chat_store.finish_run(run["id"], {"content": "Done"}, "completed")
        return chat_store.get_run(self.uid, run["id"])["request"]

    def test_memory_is_opt_in_and_artifacts_switch_reaches_the_run(self):
        self.assertFalse(chats.get_user_context(self.uid)["memory"])
        req = self.request_for()
        self.assertEqual(req["memory_mode"], "off")
        self.assertTrue(req["artifacts"])
        chats.set_user_context(self.uid, memory=True, artifacts=False)
        req = self.request_for()
        self.assertEqual(req["memory_mode"], "full")
        self.assertFalse(req["memory_readonly"])
        # Before this, the Artifacts switch never reached a run at all.
        self.assertIs(req["artifacts"], False)
        self.assertEqual(self.request_for(memory_off=True)["memory_mode"], "off")
        thread = chats.create_thread(self.uid, model="offline", tools=[])
        updated = chats.update_thread(self.uid, thread["id"], memory_off=True, expected_revision=thread["revision"])
        self.assertTrue(updated["memory_off"])
        chats.set_user_context(self.uid, memory=False, artifacts=True)


class RealTurnTest(unittest.TestCase):
    """A turn through the runtime with a captured provider: what memory adds
    to the request, and that a model's remember call is stored."""

    @classmethod
    def setUpClass(cls):
        db.connect()
        accounts.create_user("turn@example.test", "password12", admin=False, require_invite=False)
        cls.uid = int(accounts.user_by_email("turn@example.test")["id"])

    def turn(self, **thread):
        from app import chat_runs as runs, chat_runtime as runtime

        made = chats.create_thread(self.uid, model="offline", tools=[], **thread)
        run = chat_store.start_turn(self.uid, made["id"], "Remember I like terse replies", made["revision"], None)["run"]
        payloads = []
        call = [{"id": "m1", "function": {"name": "remember", "arguments": json.dumps(
            {"name": "likes-terse", "type": "user", "description": "Prefers terse replies.", "body": "Keep replies short."})}}]

        async def invoke(request, user, payload, **_kw):
            payloads.append(copy.deepcopy(payload))
            calls = call if len(payloads) == 1 and "tools" in payload else None
            message = {"content": "" if calls else "Done", **({"tool_calls": calls} if calls else {})}
            result = SimpleNamespace(status=200, body={"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]},
                                     usage={"prompt_tokens": 10, "completion_tokens": 2}, stream=None, credential_id=1)
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", result

        async def go():
            with patch.object(runtime.api, "invoke_chat", invoke), patch.object(runtime.api, "_safe_record_usage"):
                response = await runtime.stream_dashboard_chat(
                    runs.internal_request(self.uid), {"id": self.uid}, run["request"], frozen_context=run["request"])
                async for _ in response.body_iterator:
                    pass

        asyncio.run(go())
        return payloads

    def test_memory_on_and_off_in_a_real_turn(self):
        chats.set_user_context(self.uid, memory=True, artifacts=False)
        memory.save(self.uid, name="tz", type="user", description="Works in CET.", body="Berlin, CET.")
        first = self.turn()[0]
        names = [t["function"]["name"] for t in first["tools"]]
        self.assertEqual(names[-3:], ["remember", "recall", "forget"])
        self.assertNotIn("create_artifact", names)
        self.assertTrue(first["messages"][0]["content"].endswith("Memory index:\nGlobal memories:\n- tz [user] — Works in CET."))
        found, _ = memory.read(self.uid, ["likes-terse"])
        self.assertEqual(found[0]["body"], "Keep replies short.")

        off = self.turn(memory_off=True)[0]
        self.assertNotIn("remember", json.dumps(off.get("tools") or []))
        self.assertNotIn("Memory index", off["messages"][0]["content"])
        self.assertNotIn("Works in CET", json.dumps(off["messages"]))
        chats.set_user_context(self.uid, memory=False, artifacts=True)


if __name__ == "__main__":
    unittest.main()
