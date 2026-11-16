"""Agent Skills: SKILL.md frontmatter parsing, upload validation, install /
enable / disable / delete with the files and the row kept in step, per-user
isolation, load_skill path safety, crash recovery of a half-done swap, and
chat-proposed skills confirmed once by the user. Files live in a per-test
directory (``skills_dir``)."""
import io
import shutil
import zipfile
from unittest.mock import patch

import pytest

from app import chat_store, chats, db, skill_proposals, skills
from app.skill_install import content_hash, files_from_zip, parse_upload, skill_dir, sweep_partial_swaps
from app.skill_parse import SkillError, parse_skill_md
from tests.core.helpers import quick_user


def md(name="demo", desc="Use when testing skills.", extra="", body="Do the thing.\n"):
    return f"---\nname: {name}\ndescription: {desc}\n{extra}---\n{body}"


def install(uid, slug="demo", *, body="Body text.\n", extra="", **kw):
    return skills.install_files(uid, {"SKILL.md": md(slug, extra=extra, body=body).encode(), **kw.pop("files", {})},
                                origin="upload", slug=slug, **kw)


def zipped(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            if isinstance(data, zipfile.ZipInfo):
                zf.writestr(data, b"/etc/passwd")
            else:
                zf.writestr(name, data)
    return buf.getvalue()


# -- SKILL.md -----------------------------------------------------------------

def test_frontmatter_parses_name_description_flags_and_body():
    parsed = parse_skill_md(md(extra='disable-model-invocation: true  # manual only\nuser-invocable: false # hidden\n'
                                     'argument-hint: "a # b" # c\n'))
    assert (parsed["name"], parsed["body"]) == ("demo", "Do the thing.\n")
    assert parsed["description"] == "Use when testing skills."
    assert (parsed["disable_model_invocation"], parsed["user_invocable"], parsed["argument_hint"]) == (True, False, "a # b")
    folded = parse_skill_md("---\nname: PDF\ndescription: >\n  Reads PDFs.\n  Use for PDFs.\n---\n# PDF\n")
    assert (folded["name"], folded["description"]) == ("pdf", "Reads PDFs. Use for PDFs.")
    # The installing slug wins over the frontmatter name; no description falls back to the body.
    assert parse_skill_md("---\nname: demo\n---\nDo it.\n", slug="other")["name"] == "other"
    assert parse_skill_md("---\nname: demo\n---\nDo it.\n")["description"] == "Do it."
    # An unrecognized disable value keeps the skill away from the model.
    assert parse_skill_md(md(extra="disable-model-invocation: manual\n"))["disable_model_invocation"] is True
    # The closing --- must own its line; at end of file it still closes.
    assert parse_skill_md("---\nname: demo\n--- no\ndescription: d\n---\nX\n")["body"] == "X\n"
    assert parse_skill_md("---\nname: demo\ndescription: d\n---")["body"] == ""


@pytest.mark.parametrize("text", [
    "no frontmatter at all",
    "---\nname: demo\ndescription: never closed\n",
    "---\nname: ---\ndescription: d\n---\nbody\n",
    "---\nname: demo\n---\n" + "x" * (256 * 1024),
])
def test_unusable_skill_files_are_refused(text):
    with pytest.raises(SkillError):
        parse_skill_md(text)


def test_deeply_nested_frontmatter_stays_small():
    # Each level used to re-escape the one below: size doubled per level.
    nested = "\n".join(" " * (i + 1) + "a:" for i in range(60)) + "\n" + " " * 61 + '"x": "y"'
    parsed = parse_skill_md(f"---\nname: demo\ndescription: d\njunk:\n{nested}\n---\nbody\n")
    assert parsed["name"] == "demo" and len(str(parsed)) < 4096


def test_uploads_accept_one_skill_and_refuse_unsafe_archives():
    slug, files = parse_upload("pack.zip", zipped({"pack/SKILL.md": md("pack"), "pack/ref/notes.md": "n",
                                                   "pack/run.exe": "MZ"}))
    assert slug == "pack" and sorted(files) == ["SKILL.md", "ref/notes.md"]  # disallowed types dropped
    link = zipfile.ZipInfo("pack/evil.md")
    link.external_attr = 0o120777 << 16
    for archive in (zipped({"../SKILL.md": md()}),                          # traversal
                    zipped({"SKILL.md": md(), "evil.md": link}),            # symlink
                    zipped({"a/SKILL.md": md("a"), "b/SKILL.md": md("b")}),  # two skills
                    b"PK\x03\x04 not a zip"):
        with pytest.raises(SkillError):
            parse_upload("x.zip", archive)
    with pytest.raises(SkillError):
        parse_upload("SKILL.md", b"\xff\xfe broken utf-8")
    with pytest.raises(SkillError):
        files_from_zip(zipped({"SKILL.md": md(), "big.md": "x" * (256 * 1024 + 1)}))


# -- the library ---------------------------------------------------------------

def test_an_installed_skill_loads_with_its_files_and_no_host_paths(fresh_db, skills_dir):
    uid = quick_user()
    install(uid, "pack", body="Root body.\n", files={"references/foo.md": b"ref-contents\n"})
    text = skills.load(uid, "pack")
    assert "Root body." in text and "skill://pack" in text and "- references/foo.md" in text
    assert str(skills_dir) not in text
    assert "ref-contents" in skills.load(uid, "pack", file="references/foo.md")
    assert "Root body." in skills.load(uid, "pack", file="SKILL.md")
    for path in ("../etc/passwd", "/etc/passwd", "..\\x", "references/../../x.md"):
        assert skills.load(uid, "pack", file=path).startswith(skills.FILE_ERROR + "invalid skill path"), path
    assert skills.load(uid, "pack", file="missing.md").startswith(skills.FILE_ERROR)
    assert skills.load(uid, "nope") == "Unknown skill."


def test_skills_are_private_to_their_account(fresh_db, skills_dir):
    owner, other = quick_user(), quick_user("other@core.test")
    install(owner, "secret-sauce", body="Owner only.\n")
    assert skills.load(other, "secret-sauce") == "Unknown skill."
    assert "secret-sauce" not in [s["slug"] for s in skills.list_bundle(other)["skills"]]
    assert "`secret-sauce`" not in skills.catalog_for_prompt(other)
    with pytest.raises(SkillError):
        skills.delete_skill(other, "secret-sauce")
    assert skill_dir(owner, "secret-sauce").is_dir() and not skill_dir(other, "secret-sauce").exists()


def test_disabled_and_model_hidden_skills_stay_out_of_the_prompt(fresh_db, skills_dir):
    uid = quick_user()
    install(uid, "alpha", extra="disable-model-invocation: true\n", body="Quiet body.\n")
    install(uid, "bravo")
    install(uid, "charlie")
    skills.patch_skill(uid, "bravo", {"enabled": False})
    index = skills.catalog_for_prompt(uid)
    assert "`charlie`" in index and "`alpha`" not in index and "`bravo`" not in index
    assert "Quiet body." in skills.load(uid, "alpha")  # hidden from the model, still loadable on request
    assert skills.load(uid, "bravo") == "Unknown skill."
    assert skills.patch_skill(uid, "bravo", {"enabled": True})["enabled"] is True
    assert "`bravo`" in skills.catalog_for_prompt(uid)
    for bad in ({"enabled": "yes"}, {"origin": "github"}):
        with pytest.raises(SkillError):
            skills.patch_skill(uid, "bravo", bad)


def test_bundled_skills_default_and_a_user_copy_overrides_them(fresh_db, skills_dir):
    uid, other = quick_user(), quick_user("other@core.test")
    default_on = {s["slug"]: s["enabled"] for s in skills.list_bundle(uid)["skills"]}
    assert default_on["orchestrator-dispatch"] is True and default_on["ponytail"] is False
    assert skills.load(uid, "ponytail") == "Unknown skill."
    skills.patch_skill(uid, "ponytail", {"enabled": True})
    assert "Ponytail" in skills.load(uid, "ponytail")
    assert skills.load(other, "ponytail") == "Unknown skill."  # one account's switch only
    install(uid, "debug", body="My own debug.\n")
    assert "My own debug." in skills.load(uid, "debug")


def test_an_installed_copy_with_lost_files_falls_back_to_the_bundled_one(fresh_db, skills_dir):
    uid = quick_user()
    install(uid, "ponytail", body="My old copy.\n")
    install(uid, "gone")
    for slug in ("ponytail", "gone"):
        shutil.rmtree(skill_dir(uid, slug))
    text = skills.load(uid, "ponytail")
    assert "Ponytail" in text and "My old copy." not in text and "origin=bundled" in text
    assert skills.load(uid, "gone") == "Unknown skill."  # nothing bundled to fall back to


def test_reinstalling_replaces_the_tree_unless_replace_is_off(fresh_db, skills_dir):
    uid = quick_user()
    install(uid, "demo", files={"old.md": b"old"})
    with pytest.raises(SkillError) as exists:
        install(uid, "demo", replace=False)
    assert exists.value.status_code == 409
    install(uid, "demo", body="New body.\n", files={"new.md": b"new"})
    assert sorted(p.name for p in skill_dir(uid, "demo").iterdir()) == ["SKILL.md", "new.md"]
    assert skills.get_row(uid, "demo")["content_hash"] == content_hash(
        {"SKILL.md": md("demo", body="New body.\n").encode(), "new.md": b"new"})


def test_a_failed_row_write_puts_the_previous_files_back(fresh_db, skills_dir):
    uid = quick_user()
    install(uid, "demo", body="Old body.\n")
    with patch.object(skills, "_upsert", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            install(uid, "demo", body="New body.\n")
        with pytest.raises(RuntimeError):
            install(uid, "fresh")
    assert "Old body." in skills.load(uid, "demo")
    assert not skill_dir(uid, "fresh").exists()
    with patch.object(db, "tx", side_effect=RuntimeError("db down")), pytest.raises(RuntimeError):
        skills.delete_skill(uid, "demo")
    assert (skill_dir(uid, "demo") / "SKILL.md").is_file()
    skills.delete_skill(uid, "demo")
    assert skills.get_row(uid, "demo") is None and not skill_dir(uid, "demo").exists()
    assert [p.name for p in skill_dir(uid, "x").parent.iterdir()] == []  # no staging leftovers


def test_slash_invocation_needs_a_coding_chat_and_an_invocable_skill(fresh_db, skills_dir):
    uid = quick_user()
    install(uid, "deploy")
    install(uid, "hidden", extra="user-invocable: false\n")
    assert skills.parse_invoke(uid, None, "/deploy to prod", coding=True) == {"name": "deploy", "arguments": "to prod"}
    with pytest.raises(SkillError):
        skills.parse_invoke(uid, None, "/deploy", coding=False)
    assert skills.parse_invoke(uid, None, "/hidden now", coding=True) is None
    assert skills.parse_invoke(uid, None, "/compact", coding=True) is None  # reserved commands stay commands
    with pytest.raises(SkillError):
        skills.parse_invoke(uid, {"name": "undo"}, "", coding=True)
    with pytest.raises(SkillError):
        skills.parse_invoke(quick_user("other@core.test"), {"name": "deploy"}, "", coding=True)


# -- crash recovery ------------------------------------------------------------

OLD = {"SKILL.md": md(body="old body\n").encode()}
NEW = {"SKILL.md": md(body="new body\n").encode(), "notes.md": b"n"}


def _tree(root, files):
    root.mkdir(parents=True)
    for rel, blob in files.items():
        (root / rel).write_bytes(blob)


@pytest.mark.parametrize("on_disk, committed, survives", [
    ({"demo": NEW, ".old-demo-abc": OLD}, OLD, OLD),      # install whose row never committed: old tree back
    ({"demo": NEW, ".old-demo-abc": OLD}, NEW, NEW),      # committed install keeps the new tree
    ({".old-demo-abc": OLD}, OLD, OLD),                    # crash between the two renames
    ({".tmp-del-demo-abc": OLD}, OLD, OLD),                # delete whose row survived: tree back
    ({".tmp-del-demo-abc": OLD}, None, None),              # committed delete: trash cleared
    ({"demo": OLD, ".tmp-demo-abc": NEW}, OLD, OLD),       # staging dir of a failed install: cleared
])
def test_a_half_done_swap_is_rolled_to_what_the_row_says(tmp_path, on_disk, committed, survives):
    user = tmp_path / "7"
    for name, files in on_disk.items():
        _tree(user / name, files)
    sweep_partial_swaps(tmp_path, row_hash=lambda uid, slug: content_hash(committed) if committed and (uid, slug) == (7, "demo") else None)
    live = user / "demo"
    if survives is None:
        assert not live.exists()
    else:
        assert {p.name: p.read_bytes() for p in live.iterdir()} == survives
    assert [p.name for p in user.iterdir()] == ([] if survives is None else ["demo"])


# -- chat-proposed skills --------------------------------------------------------

def _proposal(uid, *, name="note-taker", auto=False):
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "make a skill", thread["revision"], None,
                                **({"auto": True, "scheduled": True} if auto else {}))["run"]
    pk = chats.resolve_thread_pk(uid, thread["id"])
    return skill_proposals.create(uid, thread_id=pk, run_id=run["id"], name=name, description="Takes notes.",
                                  body="Write notes.", files={"ref.md": "r"})


def test_a_proposed_skill_is_saved_once_on_confirm(fresh_db, skills_dir):
    uid = quick_user()
    item = _proposal(uid)
    assert item["status"] == "pending" and not skill_dir(uid, "note-taker").exists()  # nothing on disk yet
    saved = skill_proposals.confirm_proposal(uid, item["id"], {})
    assert (saved["slug"], saved["origin"], saved["enabled"]) == ("note-taker", "chat", True)
    assert "Write notes." in skills.load(uid, "note-taker")
    with pytest.raises(SkillError):
        skill_proposals.confirm_proposal(uid, item["id"], {})
    # A second tab that read the card while it was still pending loses the
    # claim: its edited copy is not installed over the saved one.
    stale = {**item, "status": "pending"}
    with patch.object(skill_proposals, "get_proposal", return_value=stale), pytest.raises(SkillError):
        skill_proposals.confirm_proposal(uid, item["id"], {"replace": True, "body": "Tampered."})
    assert "Write notes." in skills.load(uid, "note-taker")
    with pytest.raises(SkillError) as foreign:
        skill_proposals.confirm_proposal(quick_user("other@core.test"), _proposal(uid, name="other-one")["id"], {})
    assert foreign.value.status_code == 404


def test_a_proposal_over_an_existing_skill_needs_replace(fresh_db, skills_dir):
    uid = quick_user()
    install(uid, "note-taker", body="Mine.\n")
    item = _proposal(uid)
    assert item["overwrite"] is True
    with pytest.raises(SkillError) as exists:
        skill_proposals.confirm_proposal(uid, item["id"], {})
    assert exists.value.status_code == 409
    assert "Mine." in skills.load(uid, "note-taker")
    assert skill_proposals.get_proposal(uid, item["id"])["status"] == "pending"  # can still be decided
    skill_proposals.confirm_proposal(uid, item["id"], {"replace": True})
    assert "Write notes." in skills.load(uid, "note-taker")


def test_unattended_or_rejected_proposals_install_nothing(fresh_db, skills_dir):
    uid = quick_user()
    auto = _proposal(uid, auto=True)
    with pytest.raises(SkillError):
        skill_proposals.confirm_proposal(uid, auto["id"], {})
    rejected = _proposal(uid, name="rejected-one")
    assert skill_proposals.reject_proposal(uid, rejected["id"])["status"] == "rejected"
    with pytest.raises(SkillError):
        skill_proposals.confirm_proposal(uid, rejected["id"], {})
    for bad in ("undo", "title", "!!!", ""):  # reserved commands or nothing usable
        with pytest.raises(SkillError):
            _proposal(uid, name=bad)
    assert skills.list_user_rows(uid) == []
    with patch.object(skill_proposals, "PENDING_CAP_THREAD", 1):
        thread = chats.resolve_thread_pk(uid, chats.create_thread(uid, model="offline")["id"])
        skill_proposals.create(uid, thread_id=thread, name="first-one", description="d", body="b")
        with pytest.raises(SkillError) as capped:
            skill_proposals.create(uid, thread_id=thread, name="second-one", description="d", body="b")
    assert capped.value.status_code == 429
