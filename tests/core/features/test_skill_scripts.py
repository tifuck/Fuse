"""Skills with script files: copied byte for byte to a machine only after the
user allows that machine and version, never for markdown-only skills, and
third-party scripts stay off until the user approves exactly those files.
The machine, the workspace write and the permission card are fakes."""
import json

import pytest

from app import permissions, skill_sync, skills, workspace
from app.skill_parse import SkillError
from tests.core.helpers import quick_user, run_async

BIG = ("// filler\n" * 4000).encode()  # past load_skill's 24,000-character text limit


def md(name="demo"):
    return f"---\nname: {name}\ndescription: Use when testing scripts.\n---\nRun scripts/run.py.\n".encode()


def install(uid, files, *, origin="upload", slug="demo", **kw):
    return skills.install_files(uid, {"SKILL.md": md(slug), **files}, origin=origin, slug=slug, **kw)


class Machine:
    """Fakes the host lookup, the gated write and the permission card."""

    def __init__(self, monkeypatch, answer="allow", write_status="done"):
        self.asks, self.writes = [], {}
        self.answer, self.write_status = answer, write_status
        hosts = {"blk": {"id": 7, "alias": "blk"}, "ghost": {"id": 8, "alias": "ghost"}}
        monkeypatch.setattr(workspace, "resolve_host_row", lambda uid, alias, bound: hosts[alias or "blk"])

        async def execute(name, arguments, **kw):
            args = json.loads(arguments)
            if self.write_status != "done":
                return "permission mode is read_only", {"name": name, "status": "error"}
            self.writes[(args["host"], args["path"])] = args["content"].encode()
            return "ok", {"name": name, "status": "done"}

        async def ask(gate, payload, auto_judge=None):
            self.asks.append(payload)
            return self.answer

        monkeypatch.setattr(workspace, "execute", execute)
        monkeypatch.setattr(permissions, "_ask_and_wait", ask)
        monkeypatch.setattr(permissions, "bypass_enabled", lambda uid, tid: False)

    def gate(self, uid, unattended=False):
        return permissions.Gate(user_id=uid, thread_id=1, run_id="r1", requester=None,
                                unattended=unattended, notify=lambda event: None, cancel=None)

    def sync(self, uid, slug="demo", host=None, unattended=False):
        return run_async(skill_sync.sync(uid, slug, host=host, bound_host_id=7, bound_cwd="", thread_id=1,
                                         run_id="r1", gate=self.gate(uid, unattended)))


def test_a_markdown_only_skill_never_asks_or_copies(fresh_db, skills_dir, monkeypatch):
    uid = quick_user()
    install(uid, {"references/notes.md": b"# notes\n", "data.json": b"{}"})
    machine = Machine(monkeypatch)
    assert machine.sync(uid) == ""
    assert machine.asks == [] and machine.writes == {}


def test_scripts_are_copied_byte_exact_only_after_allow_and_once_per_version(fresh_db, skills_dir, monkeypatch):
    uid = quick_user()
    install(uid, {"scripts/run.py": b"print('hi')\n", "scripts/big.mjs": BIG})
    machine = Machine(monkeypatch, answer="deny")
    note = machine.sync(uid)
    assert machine.writes == {} and "denied" in note and "not on that machine" in note
    ask = machine.asks[0]
    assert (ask["perm_kind"], ask["command"], ask["host"]) == ("skill", "demo", "blk")
    assert ask["path"].startswith("/tmp/fuse/skills/demo-") and ask["description"] == "2 files"

    machine.answer = "allow"
    note = machine.sync(uid)
    folder = machine.asks[1]["path"]
    assert machine.writes == {("blk", folder + "/scripts/run.py"): b"print('hi')\n", ("blk", folder + "/scripts/big.mjs"): BIG}
    assert folder in note
    # Allowed once for this machine and version: the next load rewrites the copy without asking.
    machine.writes.clear()
    machine.sync(uid)
    assert len(machine.asks) == 2 and len(machine.writes) == 2
    # Another machine asks for itself.
    machine.sync(uid, host="ghost")
    assert len(machine.asks) == 3 and machine.asks[2]["host"] == "ghost"
    # A new version lands in a new folder and asks again.
    install(uid, {"scripts/run.py": b"print('v2')\n", "scripts/big.mjs": BIG})
    machine.sync(uid)
    assert len(machine.asks) == 4 and machine.asks[3]["path"] != folder


def test_unattended_runs_and_failed_writes_copy_nothing(fresh_db, skills_dir, monkeypatch):
    uid = quick_user()
    install(uid, {"run.sh": b"echo hi\n"})
    machine = Machine(monkeypatch)
    assert "Unattended" in machine.sync(uid, unattended=True)
    assert machine.asks == [] and machine.writes == {}
    machine.write_status = "error"
    assert "not copied" in machine.sync(uid)


def test_third_party_scripts_stay_off_until_exactly_those_files_are_approved(fresh_db, skills_dir, monkeypatch):
    uid = quick_user()
    row = install(uid, {"run.py": b"print(1)\n"}, origin="github", source="foo/bar", source_id="foo/bar/demo")
    assert row["enabled"] is False and row["review"] is True
    with pytest.raises(SkillError):
        skills.patch_skill(uid, "demo", {"enabled": True})
    review = skills.review_files(uid, "demo")
    assert [f["path"] for f in review["files"]] == ["SKILL.md", "run.py"] and review["source"] == "foo/bar"
    with pytest.raises(SkillError):
        skills.approve(uid, "demo", "stale")
    row = skills.approve(uid, "demo", review["hash"])
    assert row["enabled"] is True and row["review"] is False
    machine = Machine(monkeypatch)
    machine.sync(uid)
    assert machine.asks[0]["description"] == "1 file\nNot made by Fuse · github.com/foo/bar"
    # Changed files switch it off again; the same files keep their approval.
    row = install(uid, {"run.py": b"print(2)\n"}, origin="github", source="foo/bar", source_id="foo/bar/demo")
    assert row["enabled"] is False and skills.load(uid, "demo") == "Unknown skill."
    # A third-party skill of markdown only needs no review.
    assert install(uid, {}, origin="github", source="foo/bar", slug="plain")["enabled"] is True
