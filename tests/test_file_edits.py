"""File edits need no prior read_file: search_replace matches old_string
against the file as it is now, and write_file overwrites and says what it
replaced. Only a change landing while Fuse's own edit is in flight is refused."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from tests.test_tool_audit_fixes import FakeSFTP, sftp_patch, ws


def edit(old, new, path="a.py"):
    return {"path": path, "old_string": old, "new_string": new}


class SFTPEditTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sftp = FakeSFTP({"/repo/a.py": b"one\ntwo\n"})
        for p in (patch.object(ws, "_is_agent", return_value=False), sftp_patch(self.sftp)):
            p.start()
            self.addCleanup(p.stop)

    async def test_edit_without_read_applies(self):
        # The model may have looked at the file with sed or grep, or not at all.
        await ws._replace(1, 2, "h", edit("one", "1"), "/repo")
        self.assertEqual(self.sftp.files["/repo/a.py"], b"1\ntwo\n")

    async def test_outside_change_after_a_read_does_not_block_the_edit(self):
        # A command, the user or another agent changed the file after this
        # agent read it: old_string decides against the current content.
        await ws._read(1, 2, "h", {"path": "a.py"}, "/repo")
        self.sftp.files["/repo/a.py"] = b"ONE\ntwo\n"
        self.sftp.mtimes["/repo/a.py"] = 99
        await ws._replace(1, 2, "h", edit("two", "2"), "/repo")
        self.assertEqual(self.sftp.files["/repo/a.py"], b"ONE\n2\n")
        with self.assertRaisesRegex(ws.WorkspaceError, "old_string not found"):
            await ws._replace(1, 2, "h", edit("one", "1"), "/repo")
        self.assertEqual(self.sftp.files["/repo/a.py"], b"ONE\n2\n")

    async def test_unread_overwrite_reports_the_replaced_size(self):
        text, _ = await ws._write(1, 2, "h", {"path": "a.py", "content": "x\n"}, "/repo")
        self.assertIn("overwrote /repo/a.py (2 bytes; was 8 bytes)", text)
        self.assertEqual(self.sftp.files["/repo/a.py"], b"x\n")

    async def test_new_file_then_edit(self):
        text, _ = await ws._write(1, 2, "h", {"path": "new.py", "content": "x\n"}, "/repo")
        self.assertIn("created /repo/new.py (2 bytes)", text)
        await ws._replace(1, 2, "h", edit("x", "y", "new.py"), "/repo")
        self.assertEqual(self.sftp.files["/repo/new.py"], b"y\n")

    async def test_missing_file_edit_says_to_create_it(self):
        with self.assertRaisesRegex(ws.WorkspaceError, "no such file or directory.*write_file to create it"):
            await ws._replace(1, 2, "h", edit("a", "b", "nope.py"), "/repo")


class ConnectorEditTest(unittest.IsolatedAsyncioTestCase):
    """Connectors: an edit goes straight to the connector's replace op, which
    matches old_string against the current file."""

    def setUp(self):
        self.text = "one\ntwo\n"
        self.calls = []
        for p in (patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", self.call)):
            p.start()
            self.addCleanup(p.stop)

    async def call(self, _u, _h, op, args, seconds=30):
        self.calls.append(op)
        if op == "read":
            lines = self.text.splitlines(keepends=True)
            start = args["offset"] - 1
            return {"ok": True, "out": "".join(lines[start:start + args["limit"]])}
        if op == "replace":
            # As the connector: an ambiguous old_string, overlapping matches
            # (`aa` in `aaa`) included, is refused unless replace_all is set.
            old = args["old"]
            count = self.text.count(old)
            if count == 1 and self.text.find(old, self.text.find(old) + 1) >= 0:
                count = 2
            if not count:
                return {"ok": False, "error": "old_string not found"}
            if count > 1 and not args.get("replace_all"):
                return {"ok": False, "error": f"old_string is not unique ({count} occurrences)"}
            self.text = self.text.replace(old, args["new"], -1 if args.get("replace_all") else 1)
            reps = count if args.get("replace_all") else 1
            return {"ok": True, "out": f"{reps} replacement{'s' if reps != 1 else ''}"}
        if op == "write":
            self.text = args["content"]
            return {"ok": True, "out": "wrote"}
        if op == "exec":
            return {"ok": True, "exit": 0, "out": f"{len(self.text)}\nw\ndw\n"}
        raise AssertionError(op)

    async def test_unread_edit_needs_no_extra_round_trip(self):
        await ws._replace(1, 2, "h", edit("one", "1", "/w/a.py"), "/w")
        self.assertEqual(self.text, "1\ntwo\n")
        # The only read is the post-edit sample that numbers the edited lines.
        self.assertEqual(self.calls, ["replace", "read"])

    async def test_outside_change_after_a_read_does_not_block_the_edit(self):
        await ws._read(1, 2, "h", {"path": "/w/a.py"}, "/w")
        self.text = "ONE\ntwo\n"
        await ws._replace(1, 2, "h", edit("two", "2", "/w/a.py"), "/w")
        self.assertEqual(self.text, "ONE\n2\n")

    async def test_unread_overwrite_reports_the_replaced_size(self):
        text, _ = await ws._write(1, 2, "h", {"path": "/w/a.py", "content": "x\n"}, "/w")
        self.assertIn("overwrote /w/a.py (2 bytes; was 8 bytes)", text)
        self.assertEqual(self.text, "x\n")
        self.assertEqual(self.calls, ["exec", "write"])

    async def test_change_during_the_edit_says_nothing_was_written(self):
        async def raced(_u, _h, op, args, seconds=30):
            if op == "replace":
                return {"ok": False, "error": "file changed during edit; read it again"}
            return await self.call(_u, _h, op, args, seconds)

        with patch.object(ws, "_agent_call", raced):
            with self.assertRaises(ws.WorkspaceError) as ctx:
                await ws._replace(1, 2, "h", edit("one", "1", "/w/a.py"), "/w")
        self.assertIn("changed on disk while this edit was being written, so nothing was written",
                      ctx.exception.detail)
        self.assertNotIn("read_file", ctx.exception.detail)

    async def test_connector_errors_are_actionable(self):
        async def not_unique(_u, _h, op, args, seconds=30):
            if op == "replace":
                return {"ok": False, "error": "old_string is not unique (2 occurrences)"}
            return await self.call(_u, _h, op, args, seconds)

        with patch.object(ws, "_agent_call", not_unique):
            with self.assertRaisesRegex(ws.WorkspaceError, r"not unique \(2 occurrences\)\. Include more surrounding context lines, or pass replace_all=true"):
                await ws._replace(1, 2, "h", edit("o", "0", "/w/a.py"), "/w")

    async def test_overlapping_match_is_refused_as_ambiguous(self):
        self.text = "aaa\n"
        with self.assertRaisesRegex(ws.WorkspaceError, r"not unique \(2 occurrences\)"):
            await ws._replace(1, 2, "h", edit("aa", "b", "/w/a.py"), "/w")
        self.assertEqual(self.text, "aaa\n")

    async def test_crlf_retry_reports_the_retry_error(self):
        # A legacy connector misses the LF-only old_string in a CRLF file; the
        # CR-restored retry's own answer is what the model must be told.
        self.text = "one\r\ntwo\r\none\r\ntwo\r\n"
        with self.assertRaises(ws.WorkspaceError) as ctx:
            await ws._replace(1, 2, "h", edit("one\ntwo", "1\n2", "/w/a.py"), "/w")
        self.assertIn("not unique (2 occurrences)", ctx.exception.detail)
        self.assertNotIn("not found", ctx.exception.detail)
        self.assertEqual(self.calls.count("replace"), 2)
        self.assertEqual(self.text, "one\r\ntwo\r\none\r\ntwo\r\n")

    async def run_parked(self, first_path, second_path):
        """Two edits racing on paths; the first parks inside its replace call.
        Returns the order in which replace calls were sent and returned."""
        events = []
        release = asyncio.Event()

        async def slow(_u, _h, op, args, seconds=30):
            if op == "replace":
                events.append(("sent", args["old"]))
                if args["old"] == "one":
                    await release.wait()
                result = await self.call(_u, _h, op, args, seconds)
                events.append(("returned", args["old"]))
                return result
            return await self.call(_u, _h, op, args, seconds)

        with patch.object(ws, "_agent_call", slow):
            first = asyncio.create_task(ws._replace(1, 2, "h", edit("one", "1", first_path), "/home/u"))
            await asyncio.sleep(0.05)
            second = asyncio.create_task(ws._replace(1, 2, "h", edit("two", "2", second_path), "/home/u"))
            await asyncio.sleep(0.05)
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(self.text, "1\n2\n")
        return events

    async def test_edits_to_one_file_are_serialized(self):
        events = await self.run_parked("/home/u/a.py", "/home/u/a.py")
        self.assertEqual(events, [("sent", "one"), ("returned", "one"), ("sent", "two"), ("returned", "two")])

    async def test_home_relative_and_absolute_paths_share_the_lock(self):
        with patch.dict(ws._host_homes, {2: "/home/u"}):
            events = await self.run_parked("~/a.py", "/home/u/a.py")
        self.assertEqual(events, [("sent", "one"), ("returned", "one"), ("sent", "two"), ("returned", "two")])

    async def test_edits_to_different_files_do_not_wait(self):
        events = await self.run_parked("/home/u/a.py", "/home/u/b.py")
        self.assertEqual(events[:2], [("sent", "one"), ("sent", "two")])


class IdentityTest(unittest.TestCase):
    def test_parse_errors_say_what_to_do(self):
        with self.assertRaisesRegex(ws.WorkspaceError, "old_string is empty.*write_file to create a file"):
            ws._apply_replace("abc", "", "x", False)
        with self.assertRaisesRegex(ws.WorkspaceError, "outside the project root /repo; use a path inside it"):
            ws._confine("/etc/passwd", "/repo")


if __name__ == "__main__":
    unittest.main()
