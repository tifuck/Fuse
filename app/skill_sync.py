"""Put a skill's script files on a machine, once the user allows it.

A skill of markdown only never reaches a machine. One with script files
(skills.SCRIPT_EXT) is written, file by file through the workspace write
path, to /tmp/fuse/skills/<slug>-<hash8>/ (%TEMP%\\fuse\\skills\\... on
Windows) each time it loads, so a wiped or edited copy is replaced before the
agent runs it. The first copy of a version to a machine waits on a
permission prompt; the answer is kept per machine and files hash.
"""
from __future__ import annotations

import asyncio
import json

from . import db, permissions, skills, workspace
from .chat_prompts import TEMP_BASE_POSIX, TEMP_BASE_WIN
from .workspace import WorkspaceError


def folder_for(slug: str, digest: str, windows: bool) -> str:
    name = f"{slug}-{digest[:8]}"
    return f"{TEMP_BASE_WIN}\\skills\\{name}" if windows else f"{TEMP_BASE_POSIX}/skills/{name}"


def source_line(row: dict) -> str:
    if row.get("origin") in skills.THIRD_PARTY and row.get("source"):
        return f"From github.com/{row['source']} · not made by Fuse"
    return ""


def approved(user_id: int, host_id: int, slug: str, digest: str) -> bool:
    return db.fetchone(
        "SELECT 1 FROM skill_host_approvals WHERE user_id=? AND host_id=? AND slug=? AND files_hash=?",
        (user_id, host_id, slug, digest),
    ) is not None


def remember(user_id: int, host_id: int, slug: str, digest: str) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO skill_host_approvals(user_id, host_id, slug, files_hash, created_at) VALUES (?,?,?,?,?)",
            (user_id, host_id, slug, digest, db.now()),
        )


async def sync(user_id: int, slug: str, *, host: str | None, bound_host_id, bound_cwd: str,
               thread_id, run_id: str, gate) -> str:
    """Copy the skill's files to the machine; returns the line load_skill
    shows the model (empty for a skill of markdown only)."""
    bundle = await asyncio.to_thread(skills.script_bundle, user_id, slug)
    if bundle is None:
        return ""
    row, files, digest = bundle
    try:
        hrow = await asyncio.to_thread(workspace.resolve_host_row, user_id, host or None, bound_host_id)
    except WorkspaceError as exc:
        return f"Skill files not copied: {exc.detail}."
    if hrow is None:
        return "This skill has script files, but no machine is bound; load it again with host= to copy them to one."
    alias, host_id, windows = workspace._row_alias(hrow), workspace._row_id(hrow), workspace._is_windows_row(hrow)
    folder = folder_for(slug, digest, windows)
    texts = {}
    for rel in sorted(files):
        try:
            texts[rel] = files[rel].decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary assets stay on the server; write_file carries text only
    if not await asyncio.to_thread(approved, user_id, host_id, slug, digest):
        verdict = await permissions.review_skill(
            gate, slug=slug, host_alias=alias, host_id=host_id, folder=folder, count=len(texts), source=source_line(row),
        )
        if not verdict.allowed:
            return verdict.detail
        if verdict.resolved:
            await asyncio.to_thread(remember, user_id, host_id, slug, digest)
    # The folder name is predictable: refuse one another account made first.
    try:
        await workspace._make_fuse_temp_dir(user_id, host_id, folder, windows=windows)
    except WorkspaceError as exc:
        return f"Skill files not copied to {alias}: {exc.detail}"
    sep = "\\" if windows else "/"
    for rel, content in texts.items():
        args = {"path": folder + sep + rel.replace("/", sep), "content": content, "host": alias}
        text, rec = await workspace.execute(
            "write_file", json.dumps(args), user_id=user_id, bound_host_id=bound_host_id, bound_cwd=bound_cwd,
            run_id=run_id, thread_id=thread_id, gate=gate,
        )
        if rec.get("status") != "done":
            return f"Skill files not copied to {alias}: {text}"
    count = len(texts)
    return (f"Skill files: on {alias} in {folder} ({count} file{'' if count == 1 else 's'}, sha256 {digest[:12]}). "
            "Run them from there; do not edit or move them.")
