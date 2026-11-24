"""File tools (read_file, write_file, search_replace) through workspace.execute.

The SSH host is a fake (tests.core.tools._support): Fuse's real SFTP logic
(paging, atomic publish, in-place edits) runs against an in-memory file
system; nothing on this machine is read or written. The last tests drive
the connector path through FakeConnector on the same kind of file system.
"""
import asyncio
import json
import posixpath

import pytest

from app import db, hosts, workspace
from tests.core.tools._support import make_host, make_project


def call(h, name, **args):
    """One tool call on the bound host; returns (body without the host label, rec)."""
    text, rec = asyncio.run(workspace.execute(
        name, json.dumps(args), user_id=h.uid, bound_host_id=h.host_id, bound_cwd=h.root))
    return workspace.unlabel_host(h.alias, text), rec


def put(h, rel, data):
    return h.fs.write(posixpath.join(h.root, rel), data)


# -- read_file ---------------------------------------------------------------

def test_read_numbers_lines_and_says_where_the_file_ends(ssh_host):
    put(ssh_host, "a.txt", "alpha\nbeta\r\ngamma\n")
    body, rec = call(ssh_host, "read_file", path="a.txt")

    assert rec["status"] == "done"
    assert rec["path"] == posixpath.join(ssh_host.root, "a.txt")
    # The CR of a CRLF line is not shown (the model could not reproduce it).
    assert body == "1: alpha\n2: beta\n3: gamma\n\n(End of file — 3 lines.)"


def test_read_window_continues_exactly_where_it_stopped(ssh_host):
    put(ssh_host, "n.txt", "".join(f"line{i}\n" for i in range(1, 11)))

    body, _ = call(ssh_host, "read_file", path="n.txt", offset=4, limit=3)
    assert body.splitlines()[:3] == ["4: line4", "5: line5", "6: line6"]
    assert "(Showing lines 4-6" in body and body.endswith("Use offset=7 to continue.)")

    last, _ = call(ssh_host, "read_file", path="n.txt", offset=10, limit=3)
    assert last == "10: line10\n\n(End of file — 10 lines.)"

    beyond, rec = call(ssh_host, "read_file", path="n.txt", offset=11)
    assert rec["status"] == "error" and "beyond the end of the file" in beyond


def test_read_caps_a_page_by_lines_and_by_bytes(ssh_host):
    put(ssh_host, "long.txt", "".join(f"{i}\n" for i in range(1, 2501)))
    body, _ = call(ssh_host, "read_file", path="long.txt")
    assert body.splitlines()[1999] == "2000: 2000"
    assert "2001: 2001" not in body
    assert "Use offset=2001 to continue." in body

    put(ssh_host, "wide.txt", "".join(("x" * 200) + "\n" for _ in range(1000)))
    body, _ = call(ssh_host, "read_file", path="wide.txt")
    shown = [line for line in body.splitlines() if line.startswith(tuple("0123456789")) and ": x" in line]
    assert len(body.encode()) <= 52 * 1024
    assert "Output capped at 50KB" in body
    assert f"Use offset={len(shown) + 1} to continue." in body


def test_a_page_that_fills_the_byte_window_exactly_at_eof_is_the_end(ssh_host):
    put(ssh_host, "wide.txt", ("x" * 16383 + "\n") * 4)  # exactly one 64 KiB SFTP window
    body, _ = call(ssh_host, "read_file", path="wide.txt")
    assert body.endswith("(End of file — 4 lines.)")


def test_raw_reads_keep_crlf_line_endings(ssh_host):
    put(ssh_host, "win.txt", "one\r\ntwo\r\nthree")
    for args, want in (({}, "one\r\ntwo\r\nthree"), ({"offset": 2, "limit": 1}, "two\r\n")):
        text, _ = asyncio.run(workspace.execute(
            "read_file", json.dumps({"path": "win.txt", **args}), user_id=ssh_host.uid,
            bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root, raw=True))
        assert workspace.unlabel_host(ssh_host.alias, text) == want


def test_read_refuses_directories_missing_files_and_binary_data(ssh_host):
    ssh_host.fs.makedirs(posixpath.join(ssh_host.root, "sub"))
    put(ssh_host, "blob.bin", b"\x7fELF\x00\x01\x02")

    text, rec = call(ssh_host, "read_file", path="sub")
    assert rec["status"] == "error" and "use list_dir" in text
    text, rec = call(ssh_host, "read_file", path="nope.txt")
    assert rec["status"] == "error" and "no such file" in text
    text, rec = call(ssh_host, "read_file", path="blob.bin")
    assert rec["status"] == "error" and "binary" in text


def test_read_image_returns_a_decoded_frame_not_bytes(ssh_host):
    # One real decode (a child interpreter with PIL): ~0.3 s.
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(out, format="PNG")
    put(ssh_host, "shot.png", out.getvalue())

    body, rec = call(ssh_host, "read_file", path="shot.png")

    assert rec["status"] == "done"
    assert rec["image"]["mime"] == "image/png"
    assert (rec["image"]["width"], rec["image"]["height"]) == (40, 20)
    assert body.startswith("Image: PNG, 40×20")
    text, rec = call(ssh_host, "read_file", path="shot.png", offset=2)
    assert rec["status"] == "error" and "only to text files" in text


# -- write_file --------------------------------------------------------------

def test_write_creates_then_overwrites_and_reports_the_clobber(ssh_host):
    body, rec = call(ssh_host, "write_file", path="new/deep/f.txt", content="héllo\n")
    path = posixpath.join(ssh_host.root, "new/deep/f.txt")
    assert rec["status"] == "done"
    assert body == f"created {path} (7 bytes)"
    assert ssh_host.fs.read(path) == "héllo\n".encode()

    body, rec = call(ssh_host, "write_file", path="new/deep/f.txt", content="x")
    assert body == f"overwrote {path} (1 bytes; was 7 bytes)"
    assert ssh_host.fs.read(path) == b"x"
    # Atomic publish leaves no sibling temp file behind.
    assert ssh_host.fs.listdir(posixpath.dirname(path)) == ["f.txt"]


def test_overwrite_keeps_the_existing_file_mode(ssh_host):
    path = put(ssh_host, "run.sh", "#!/bin/sh\n")
    ssh_host.fs.chmod(path, 0o750)

    _, rec = call(ssh_host, "write_file", path="run.sh", content="#!/bin/sh\necho hi\n")

    assert rec["status"] == "done"
    assert ssh_host.fs.mode(path) == 0o750
    _, rec = call(ssh_host, "write_file", path="new.sh", content="x")
    assert ssh_host.fs.mode(posixpath.join(ssh_host.root, "new.sh")) == 0o666  # new: 0666 less the host umask


def test_write_refuses_directories_nul_bytes_and_oversized_content(ssh_host):
    ssh_host.fs.makedirs(posixpath.join(ssh_host.root, "d"))
    text, rec = call(ssh_host, "write_file", path="d", content="x")
    assert rec["status"] == "error"
    assert ssh_host.fs.isdir(posixpath.join(ssh_host.root, "d"))
    assert ssh_host.fs.listdir(posixpath.join(ssh_host.root, "d")) == []

    text, rec = call(ssh_host, "write_file", path="z.txt", content="a\x00b")
    assert rec["status"] == "error" and "NUL" in text
    text, rec = call(ssh_host, "write_file", path="z.txt", content="x" * 400_001)
    assert rec["status"] == "error" and "400000" in text
    assert not ssh_host.fs.exists(posixpath.join(ssh_host.root, "z.txt"))


# -- search_replace ----------------------------------------------------------

def test_replace_edits_one_exact_match_and_shows_numbered_lines(ssh_host):
    path = put(ssh_host, "m.py", "a = 1\nb = 2\nc = 3\n")

    body, rec = call(ssh_host, "search_replace", path="m.py", old_string="b = 2", new_string="b = 20")

    assert rec["status"] == "done"
    assert ssh_host.fs.read(path) == b"a = 1\nb = 20\nc = 3\n"
    assert body.startswith(f"updated {path} (1 replacement):")
    assert "2: b = 20" in body


def test_an_ambiguous_match_is_refused_and_replace_all_takes_every_one(ssh_host):
    path = put(ssh_host, "dup.txt", "x\ny\nx\n")

    text, rec = call(ssh_host, "search_replace", path="dup.txt", old_string="x", new_string="z")
    assert rec["status"] == "error"
    assert "not unique (2 occurrences, at lines 1, 3)" in text
    assert ssh_host.fs.read(path) == b"x\ny\nx\n"

    body, rec = call(ssh_host, "search_replace", path="dup.txt", old_string="x", new_string="z", replace_all=True)
    assert rec["status"] == "done" and "(2 replacements)" in body
    assert ssh_host.fs.read(path) == b"z\ny\nz\n"


def test_a_missing_match_changes_nothing_and_points_at_the_closest_text(ssh_host):
    path = put(ssh_host, "f.py", "def f():\n    return 1\n")

    text, rec = call(ssh_host, "search_replace", path="f.py", old_string="def f():\n  return 1", new_string="x")

    assert rec["status"] == "error"
    assert text.startswith("old_string not found")
    assert "Closest text in the file (lines 1-2" in text
    assert ssh_host.fs.read(path) == b"def f():\n    return 1\n"


def test_crlf_file_accepts_lf_edits_and_keeps_its_line_endings(ssh_host):
    path = put(ssh_host, "win.txt", "one\r\ntwo\r\nthree\r\n")

    _, rec = call(ssh_host, "search_replace", path="win.txt", old_string="one\ntwo", new_string="1\n2\n2b")

    assert rec["status"] == "done"
    assert ssh_host.fs.read(path) == b"1\r\n2\r\n2b\r\nthree\r\n"


def test_several_edits_apply_in_order_or_not_at_all(ssh_host):
    path = put(ssh_host, "e.txt", "alpha beta gamma\n")

    _, rec = call(ssh_host, "search_replace", path="e.txt", edits=[
        {"old_string": "alpha", "new_string": "ALPHA"},
        {"old_string": "ALPHA beta", "new_string": "AB"},
    ])
    assert rec["status"] == "done"
    assert ssh_host.fs.read(path) == b"AB gamma\n"

    text, rec = call(ssh_host, "search_replace", path="e.txt", edits=[
        {"old_string": "gamma", "new_string": "G"},
        {"old_string": "missing", "new_string": "M"},
    ])
    assert rec["status"] == "error"
    assert text.startswith("edit 2 of 2: old_string not found") and "No edits were applied." in text
    assert ssh_host.fs.read(path) == b"AB gamma\n"


def test_replace_refuses_missing_files_and_no_op_edits(ssh_host):
    text, rec = call(ssh_host, "search_replace", path="ghost.txt", old_string="a", new_string="b")
    assert rec["status"] == "error" and "write_file" in text
    assert not ssh_host.fs.exists(posixpath.join(ssh_host.root, "ghost.txt"))

    put(ssh_host, "same.txt", "a\n")
    text, rec = call(ssh_host, "search_replace", path="same.txt", old_string="a", new_string="a")
    assert rec["status"] == "error" and "identical" in text


def test_concurrent_edits_of_one_file_are_serialized_and_none_is_lost(ssh_host):
    path = put(ssh_host, "c.txt", "".join(f"k{i} = 0\n" for i in range(6)))

    async def edits():
        return await asyncio.gather(*(
            workspace.execute("search_replace", json.dumps({"path": "c.txt", "old_string": f"k{i} = 0",
                                                            "new_string": f"k{i} = 1"}),
                              user_id=ssh_host.uid, bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root)
            for i in range(6)
        ))

    results = asyncio.run(edits())

    assert [rec["status"] for _, rec in results] == ["done"] * 6
    assert ssh_host.fs.read(path) == "".join(f"k{i} = 1\n" for i in range(6)).encode()


# -- confinement and modes ---------------------------------------------------

def test_paths_outside_the_working_root_are_refused_without_a_gate(ssh_host):
    outside = ssh_host.fs.write("/srv/secret.txt", "top secret\n")

    for path in ("../secret.txt", outside, "sub/../../secret.txt"):
        text, rec = call(ssh_host, "read_file", path=path)
        assert rec["status"] == "error" and "outside the project root" in text, path
        assert "top secret" not in text
    text, rec = call(ssh_host, "write_file", path="../planted.txt", content="x")
    assert rec["status"] == "error"
    assert not ssh_host.fs.exists("/srv/planted.txt")
    text, rec = call(ssh_host, "read_file", path="a\nb")
    assert rec["status"] == "error" and "control characters" in text


def test_a_sibling_folder_sharing_the_root_prefix_is_outside(ssh_host):
    sibling = ssh_host.root + "-other"
    ssh_host.fs.write(f"{sibling}/s.txt", "sibling secret\n")

    for path in (f"{sibling}/s.txt", f"../{posixpath.basename(ssh_host.root)}-other/s.txt"):
        text, rec = call(ssh_host, "read_file", path=path)
        assert rec["status"] == "error" and "outside the project root" in text, path
        assert "sibling secret" not in text
    _, rec = call(ssh_host, "write_file", path=f"{sibling}/planted.txt", content="x")
    assert rec["status"] == "error" and not ssh_host.fs.exists(f"{sibling}/planted.txt")


def test_a_named_other_host_uses_its_own_working_root(ssh_host):
    other_root = "/srv/other"
    make_host(ssh_host.uid, "box2", default_cwd=other_root)
    put(ssh_host, "f.txt", "project copy\n")
    ssh_host.fs.write(f"{other_root}/f.txt", "other host copy\n")

    body, rec = call(ssh_host, "read_file", path="f.txt", host="box2")

    assert rec["status"] == "done" and "1: other host copy" in body
    text, rec = call(ssh_host, "read_file", path=posixpath.join(ssh_host.root, "f.txt"), host="box2")
    assert rec["status"] == "error" and "project copy" not in text  # the project root is the bound host's only


def test_a_disabled_host_runs_nothing(ssh_host):
    put(ssh_host, "f.txt", "data\n")
    db.execute("UPDATE user_hosts SET disabled=1 WHERE id=?", (ssh_host.host_id,))

    text, rec = call(ssh_host, "read_file", path="f.txt")
    assert rec["status"] == "error" and "host is disabled" in text
    text, rec = call(ssh_host, "read_file", path="f.txt", host="box")
    assert rec["status"] == "error" and "unknown host alias" in text
    _, rec = call(ssh_host, "run_terminal_command", command="touch marker")
    assert rec["status"] == "error"
    assert ssh_host.shell.raw == []  # the host was never asked to run anything


def test_read_only_mode_blocks_every_mutating_tool(ssh_host):
    path = put(ssh_host, "keep.txt", "v1\n")
    hosts.set_settings(ssh_host.uid, permission_mode="read_only")

    for name, args in (("write_file", {"path": "keep.txt", "content": "v2"}),
                       ("search_replace", {"path": "keep.txt", "old_string": "v1", "new_string": "v2"}),
                       ("run_terminal_command", {"command": "echo v2 > keep.txt"})):
        text, rec = call(ssh_host, name, **args)
        assert rec["status"] == "error" and rec["kind"] == "not_allowed", name
    assert ssh_host.fs.read(path) == b"v1\n"
    _, rec = call(ssh_host, "read_file", path="keep.txt")
    assert rec["status"] == "done"


def test_a_read_only_project_blocks_writes_inside_it(ssh_host):
    path = put(ssh_host, "src/x.txt", "v1\n")
    make_project(ssh_host.uid, ssh_host.host_id, ssh_host.root, permission_mode="read_only")

    text, rec = call(ssh_host, "write_file", path="src/x.txt", content="v2")

    assert rec["status"] == "error" and "read-only" in text
    assert ssh_host.fs.read(path) == b"v1\n"


def test_a_home_path_is_outside_a_project_root(ssh_host):
    ssh_host.fs.write(f"{ssh_host.home}/notes.txt", "home words\n")

    text, rec = call(ssh_host, "read_file", path="~/notes.txt")

    assert rec["status"] == "error" and "outside the project root" in text and "home words" not in text


def test_unknown_or_reserved_host_aliases_are_refused(ssh_host):
    for alias, why in (("nowhere", "unknown host alias"), ("localhost", "not allowed")):
        text, rec = call(ssh_host, "read_file", path="x", host=alias)
        assert rec["status"] == "error" and why in text


# -- connector path ----------------------------------------------------------

def test_connector_read_of_an_exact_page_is_not_reported_as_continuing(agent_host):
    put(agent_host, "three.txt", "a\nb\nc\n")
    body, _ = call(agent_host, "read_file", path="three.txt", limit=3)
    assert body.endswith("(End of file — 3 lines.)")

    put(agent_host, "four.txt", "a\nb\nc\nd\n")
    body, _ = call(agent_host, "read_file", path="four.txt", limit=3)
    assert body.endswith("Use offset=4 to continue.)")
    assert "4: d" not in body
    # One look one line ahead answers both, not a second request per read.
    assert [op for op, _ in agent_host.connector.calls].count("read") == 2


def test_connector_write_creates_a_missing_parent_first(agent_host):
    body, rec = call(agent_host, "write_file", path="made/here/f.txt", content="ok\n")

    path = posixpath.join(agent_host.root, "made/here/f.txt")
    assert rec["status"] == "done" and body == f"created {path} (3 bytes)"
    assert agent_host.fs.read(path) == b"ok\n"


def test_connector_crlf_edit_is_retried_with_crlf_and_never_as_a_write(agent_host):
    path = put(agent_host, "w.txt", "one\r\ntwo\r\n")

    _, rec = call(agent_host, "search_replace", path="w.txt", old_string="one\ntwo", new_string="1\n2")
    assert rec["status"] == "done"
    assert agent_host.fs.read(path) == b"1\r\n2\r\n"

    text, rec = call(agent_host, "search_replace", path="w.txt", old_string="\r\n", new_string="\n")
    assert rec["status"] == "error" and "not unique" in text
    assert "write" not in [op for op, _ in agent_host.connector.calls]
    assert agent_host.fs.read(path) == b"1\r\n2\r\n"


def test_browse_has_no_up_from_a_windows_drive_or_share_root(agent_host):
    for path in ("C:\\", "\\\\srv\\share\\"):
        agent_host.connector.overrides["browse"] = {
            "ok": True, "out": json.dumps({"path": path, "parent": "", "home": "", "entries": []})}
        view = asyncio.run(workspace.browse(agent_host.uid, agent_host.host_id, path))
        assert view["path"] == path and view["parent"] == ""


def test_the_fake_host_itself_refuses_unconfined_paths(ssh_host):
    """Defense in depth: whatever app code decides, the fake file system only
    takes clean absolute paths (and has no route to the real machine)."""
    for path in ("relative.txt", "~/x", "/srv/app/../etc/passwd", "//etc/passwd"):
        with pytest.raises(AssertionError, match="FakeFS refuses"):
            ssh_host.fs.check(path)
