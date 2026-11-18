"""grep, glob and list_dir.

grep and glob send one bounded search command to the host. The fake host
records it and answers with scripted output, so these tests check two
things: what Fuse asks the host to search (scope, include, ignore and size
rules), and what it makes of the answer (re-rooting, secret withholding,
caps). Nothing is executed. list_dir walks the fake host's in-memory files
over SFTP.
"""
import asyncio
import json
import posixpath

from app import workspace


def run(h, name, **args):
    text, rec = asyncio.run(workspace.execute(
        name, json.dumps(args), user_id=h.uid, bound_host_id=h.host_id, bound_cwd=h.root))
    return workspace.unlabel_host(h.alias, text), rec


def call(h, name, **args):
    text, rec = run(h, name, **args)
    assert rec["status"] == "done", (text, rec)
    return text


def rows(body):
    """Result lines without the notes Fuse appends in parentheses."""
    return sorted(line for line in body.splitlines() if line and not line.startswith("("))


def answer(h, out):
    """Script the host's answer to the next search command."""
    h.shell.on(lambda command: "FUSE_DL" in command, out=out)


def asked(h):
    [command] = h.shell.asked
    return command


def test_a_directory_search_skips_vendor_dirs_ignored_and_huge_files(ssh_host):
    answer(ssh_host, "./app/main.py\n./tests/main.py\n")
    assert rows(call(ssh_host, "grep", pattern="needle", output_mode="files_with_matches")) == [
        "app/main.py", "tests/main.py"]

    command = asked(ssh_host)
    assert "TGT=.;" in command and "-- needle" in command
    assert "--glob '!node_modules'" in command and "--glob '!.git/**'" in command
    assert "--max-filesize 1M" in command and "--no-ignore" not in command
    assert "-size -1025k" in command  # the grep fallback keeps the same size rule


def test_no_ignore_and_a_named_vendor_dir_are_searched(ssh_host):
    answer(ssh_host, "")
    call(ssh_host, "grep", pattern="needle", no_ignore=True)
    answer(ssh_host, "")
    call(ssh_host, "grep", pattern="needle", path="node_modules")

    with_ignored, vendor = ssh_host.shell.asked
    assert "--no-ignore" in with_ignored and "if false; then git ls-files" in with_ignored
    assert "TGT=node_modules;" in vendor and "--glob '!node_modules'" not in vendor


def test_include_is_relative_to_path_and_lifts_the_size_cap(ssh_host):
    answer(ssh_host, "./web/main.py:1:needle web\n")
    body = call(ssh_host, "grep", pattern="needle", path="app", include="app/web/*.py")

    command = asked(ssh_host)
    assert "TGT=app;" in command and "--glob 'web/*.py'" in command
    assert "--max-filesize" not in command and "-size -1025k" not in command
    assert rows(body) == ["app/web/main.py:1:needle web"]  # re-rooted at the searched path


def test_include_cannot_reach_outside_the_searched_path(ssh_host):
    for include in ("../x", "/etc/passwd", "sub/../../x", "~/x"):
        text, rec = run(ssh_host, "grep", pattern="root", include=include)
        assert rec["status"] == "error" and "inside the searched path" in text, include
    assert ssh_host.shell.raw == []


def test_grep_content_lines_carry_file_and_line_number(ssh_host):
    answer(ssh_host, "./web/main.py:1:needle web\n./web/ui.js:1:needle js\n")
    body = call(ssh_host, "grep", pattern="needle (web|js)", path="app")
    assert rows(body) == ["app/web/main.py:1:needle web", "app/web/ui.js:1:needle js"]
    assert "-n -m 50" in asked(ssh_host)


def test_secret_file_matches_are_withheld_from_directory_searches(ssh_host):
    answer(ssh_host, "./.env:1:TOKEN=needle-secret\n./app.py:3:needle = 1\n")
    body = call(ssh_host, "grep", pattern="needle", no_ignore=True)
    assert "TOKEN" not in body and "app.py:3:needle = 1" in body
    assert "withheld" in body

    # Named directly (the permission gate's secret prompt covers this), it is searched.
    answer(ssh_host, "./.env:1:TOKEN=needle-secret\n")
    assert "TOKEN=needle-secret" in call(ssh_host, "grep", pattern="needle", path=".env")


def test_context_lines_of_secret_files_are_withheld_too(ssh_host):
    answer(ssh_host, "./.env-1-TOKEN=abc\n./.env:2:needle here\n--\n./app.py-1-x = 1\n./app.py:2:needle here\n")

    body = call(ssh_host, "grep", pattern="needle here", no_ignore=True, context=1)

    assert "TOKEN" not in body and "abc" not in body
    assert "app.py:2:needle here" in body and "app.py-1-x = 1" in body
    assert "withheld" in body


def test_grep_caps_matches_and_says_how_to_see_more(ssh_host):
    answer(ssh_host, "".join(f"./many.txt:{i}:hit {i}\n" for i in range(1, 31)))

    body = call(ssh_host, "grep", pattern="hit", max_matches=5)

    assert len(rows(body)) == 5
    assert "(showing the first 5 matches" in body
    assert "-m 5" in asked(ssh_host)


def test_a_line_is_withheld_when_any_reading_of_its_path_is_a_secret_file(ssh_host):
    # Context lines whose text holds :N:, and file names holding -N-, read
    # as other paths; every reading is checked.
    answer(ssh_host, "./.pgpass-1-db.local:5432:app:PGPASS_LINE\n./.pgpass:2:needle\n--\n"
                     "./deploy-1-prod.pem-4-PEM_LINE\n./deploy-1-prod.pem:5:needle\n--\n./app.py:1:needle\n")

    body = call(ssh_host, "grep", pattern="needle", no_ignore=True, context=1)

    assert "PGPASS_LINE" not in body and "PEM_LINE" not in body and "pgpass" not in body
    assert rows(body) == ["app.py:1:needle"]
    assert "(2 result(s)" in body


def test_context_lines_with_times_or_ports_do_not_use_up_the_match_cap(ssh_host):
    answer(ssh_host, "./a.py-1-at 12:30:00\n./a.py:2:needle\n./a.py-3-host:6379:0\n--\n"
                     "./b.py-9-x\n./b.py:10:needle\n./b.py-11-y\n")

    body = call(ssh_host, "grep", pattern="needle", max_matches=2, context=1)

    assert {"a.py:2:needle", "b.py:10:needle", "a.py-3-host:6379:0"} <= set(rows(body))


def test_a_result_of_exactly_the_cap_is_not_called_partial(ssh_host):
    from app.workspace_ops.search_render import _format_search_lines, _glob_result

    answer(ssh_host, "")
    call(ssh_host, "grep", pattern="hit", max_matches=5)
    assert "head -n 6" in asked(ssh_host)  # one past the cap tells full from cut

    def grep(out):
        return _format_search_lines(out, root="", windows=False, grep=True, hide_secrets=False,
                                    cap_lines=2, probed=True)

    assert grep("a.py:1:x\nb.py:2:y\n") == (["a.py:1:x", "b.py:2:y"], 0, False)
    assert grep("a.py:1:x\nb.py:2:y\nc.py:3:z\n") == (["a.py:1:x", "b.py:2:y"], 0, True)
    exact = _glob_result("".join(f"f{i:03d}\n" for i in range(400)), probed=True)
    over = _glob_result("".join(f"f{i:03d}\n" for i in range(401)), probed=True)
    assert "(showing" not in exact and len(rows(exact)) == 400
    assert "(showing the first 400 paths" in over and len(rows(over)) == 400


def test_glob_patterns_are_relative_to_path(ssh_host):
    answer(ssh_host, "./web/main.py\n")
    assert rows(call(ssh_host, "glob", pattern="app/web/*.py", path="app")) == ["app/web/main.py"]
    command = asked(ssh_host)
    assert "TGT=app;" in command and "-g 'web/*.py'" in command and "--glob '!node_modules'" in command


def test_glob_refuses_patterns_that_escape_or_are_not_globs(ssh_host):
    for pattern, why in (("../*.py", "outside"), ("/etc/*", "outside"), ("*.py | sh", "unsupported"),
                         ("*.py, *.js", "unsupported"), ("$(id)", "unsupported")):
        text, rec = run(ssh_host, "glob", pattern=pattern)
        assert rec["status"] == "error" and why in text, pattern
    assert ssh_host.shell.raw == []


def test_list_dir_marks_directories_and_sorts(ssh_host):
    for rel in ("app/main.py", "node_modules/pkg/x.js", ".env", "README.md"):
        ssh_host.fs.write(posixpath.join(ssh_host.root, rel), "x")

    assert rows(call(ssh_host, "list_dir")) == [".env", "README.md", "app/", "node_modules/"]
    assert ssh_host.shell.raw == []  # a listing reads the file system, it runs nothing


def test_deep_listing_lists_but_does_not_expand_vendor_directories(ssh_host):
    for rel in ("app/web/ui.js", "tests/main.py", "node_modules/pkg/main.py", ".git/HEAD"):
        ssh_host.fs.write(posixpath.join(ssh_host.root, rel), "x")

    listed = rows(call(ssh_host, "list_dir", depth=3))

    assert {"app/web/ui.js", "tests/main.py", "node_modules/", ".git/"} <= set(listed)
    assert not any(name.startswith(("node_modules/", ".git/")) for name in listed
                   if name not in ("node_modules/", ".git/"))


def test_list_dir_caps_large_directories_alphabetically(ssh_host):
    for i in range(450):
        ssh_host.fs.write(posixpath.join(ssh_host.root, f"big/f{i:03d}"), "")

    body = call(ssh_host, "list_dir", path="big")

    shown = rows(body)
    assert len(shown) == 400 and shown[0] == "f000" and shown[-1] == "f399"
    assert "(showing 400 of 450 entries" in body
