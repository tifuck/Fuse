"""grep, glob and list_dir: what a search returns from a real tree.

The generated search commands run in a local bash (LocalConn), so include
scoping, .gitignore, vendor-directory and size rules are checked against the
files themselves, not against the command text.
"""
import asyncio
import json
import os
import shutil
import subprocess

import pytest

from app import workspace


def call(h, name, **args):
    text, rec = asyncio.run(workspace.execute(
        name, json.dumps(args), user_id=h.uid, bound_host_id=h.host_id, bound_cwd=h.root))
    assert rec["status"] == "done", (text, rec)
    return workspace.unlabel_host(h.alias, text)


def rows(body):
    """Result lines without the notes Fuse appends in parentheses."""
    return sorted(line for line in body.splitlines() if line and not line.startswith("("))


@pytest.fixture
def tree(ssh_host):
    files = {
        "app/main.py": "needle app\n",
        "app/web/main.py": "needle web\n",
        "app/web/ui.js": "needle js\n",
        "tests/main.py": "needle tests\n",
        "ignored.py": "needle ignored\n",
        "node_modules/pkg/main.py": "needle vendor\n",
        "big.py": "x = 1\n" * 200_000 + "needle big\n",  # > 1 MB
        ".env": "TOKEN=needle-secret\n",
    }
    for rel, body in files.items():
        path = os.path.join(ssh_host.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)
    with open(os.path.join(ssh_host.root, ".gitignore"), "w") as f:
        f.write("ignored.py\n")
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q", ssh_host.root], check=True)
    return ssh_host


def files(h, **args):
    return rows(call(h, "grep", pattern="needle", output_mode="files_with_matches", **args))


def test_a_directory_search_skips_ignored_vendor_huge_and_secret_files(tree):
    assert files(tree, path=".") == ["app/main.py", "app/web/main.py", "app/web/ui.js", "tests/main.py"]
    assert files(tree, path=".", no_ignore=True) == [
        "app/main.py", "app/web/main.py", "app/web/ui.js", "ignored.py", "tests/main.py"]
    # Naming the vendor directory searches it.
    assert files(tree, path="node_modules") == ["node_modules/pkg/main.py"]


def test_include_is_relative_to_path_and_lifts_the_size_cap(tree):
    assert files(tree, path="app", include="main.py") == ["app/main.py"]
    assert files(tree, path=".", include="main.py") == ["app/main.py", "app/web/main.py", "tests/main.py"]
    assert files(tree, path="app", include="web/*.py") == ["app/web/main.py"]
    assert files(tree, path="app", include="*.{py,js}") == ["app/main.py", "app/web/main.py", "app/web/ui.js"]
    assert files(tree, path=".", include="*.py") == ["app/main.py", "app/web/main.py", "big.py", "tests/main.py"]
    assert files(tree, path="app/main.py") == ["app/main.py"]


def test_include_cannot_reach_outside_the_searched_path(tree):
    for include in ("../x", "/etc/passwd", "sub/../../x"):
        text, rec = asyncio.run(workspace.execute(
            "grep", json.dumps({"pattern": "root", "include": include}),
            user_id=tree.uid, bound_host_id=tree.host_id, bound_cwd=tree.root))
        assert rec["status"] == "error" and "inside the searched path" in text, include


def test_grep_content_lines_carry_file_and_line_number(tree):
    body = call(tree, "grep", pattern="needle (web|js)", path="app")
    assert rows(body) == ["app/web/main.py:1:needle web", "app/web/ui.js:1:needle js"]


def test_secret_file_matches_are_withheld_from_directory_searches(tree):
    body = call(tree, "grep", pattern="needle-secret", path=".", no_ignore=True)
    assert "TOKEN" not in body
    assert "withheld" in body
    # Named directly (the permission gate's secret prompt covers this), it is searched.
    assert "TOKEN=needle-secret" in call(tree, "grep", pattern="needle-secret", path=".env")


def test_context_lines_of_secret_files_are_withheld_too(ssh_host):
    for name, body in ((".env", "TOKEN=abc\nneedle here\n"), ("app.py", "x = 1\nneedle here\n")):
        with open(os.path.join(ssh_host.root, name), "w") as f:
            f.write(body)

    body = call(ssh_host, "grep", pattern="needle here", path=".", no_ignore=True, context=1)

    assert "TOKEN" not in body and "abc" not in body
    assert "app.py:2:needle here" in body and "app.py-1-x = 1" in body
    assert "withheld" in body


def test_grep_caps_matches_and_says_how_to_see_more(ssh_host):
    with open(os.path.join(ssh_host.root, "many.txt"), "w") as f:
        f.write("".join(f"hit {i}\n" for i in range(30)))

    body = call(ssh_host, "grep", pattern="hit", max_matches=5)

    assert len(rows(body)) == 5
    assert "(showing the first 5 matches" in body


def test_glob_patterns_are_relative_to_path(tree):
    assert rows(call(tree, "glob", pattern="web/*.py", path="app")) == ["app/web/main.py"]
    assert rows(call(tree, "glob", pattern="**/*.{py,js}", path="app")) == [
        "app/main.py", "app/web/main.py", "app/web/ui.js"]
    assert rows(call(tree, "glob", pattern="*.py")) == ["app/main.py", "app/web/main.py", "big.py", "tests/main.py"]


def test_glob_refuses_patterns_that_escape_or_are_not_globs(tree):
    for pattern, why in (("../*.py", "outside"), ("/etc/*", "outside"), ("*.py | sh", "unsupported"),
                         ("*.py, *.js", "unsupported")):
        text, rec = asyncio.run(workspace.execute(
            "glob", json.dumps({"pattern": pattern}),
            user_id=tree.uid, bound_host_id=tree.host_id, bound_cwd=tree.root))
        assert rec["status"] == "error" and why in text, pattern


def test_list_dir_marks_directories_and_sorts(tree):
    body = call(tree, "list_dir")
    assert rows(body) == sorted([".env", ".git/", ".gitignore", "app/", "big.py", "ignored.py",
                                 "node_modules/", "tests/"])


def test_deep_listing_lists_but_does_not_expand_vendor_directories(tree):
    listed = rows(call(tree, "list_dir", depth=3))
    assert "app/web/ui.js" in listed and "tests/main.py" in listed
    assert "node_modules/" in listed
    assert ".git/" in listed
    assert not any(name.startswith(("node_modules/", ".git/")) for name in listed
                   if name not in ("node_modules/", ".git/"))


def test_list_dir_caps_large_directories_alphabetically(ssh_host):
    big = os.path.join(ssh_host.root, "big")
    os.makedirs(big)
    for i in range(450):
        open(os.path.join(big, f"f{i:03d}"), "w").close()

    body = call(ssh_host, "list_dir", path="big")

    shown = rows(body)
    assert len(shown) == 400 and shown[0] == "f000" and shown[-1] == "f399"
    assert "(showing 400 of 450 entries" in body
