"""Per-chat temp dirs: naming, the prompt line, and creating a command's cwd."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import workspace as ws
from app.chat_prompts import build_system_prompt, chat_temp_dir, temp_frame
from app.permissions import in_fuse_temp

CHILD_RUN = "0f9e8d7c-6b5a-4321-8765-0123456789ab"


class ChatTempDirTest(unittest.TestCase):
    def test_name_is_short_stable_and_per_chat(self):
        name = chat_temp_dir(42, "2026-11-12T10:00:00Z")
        self.assertRegex(name, r"^c-[0-9a-f]{8}$")
        self.assertEqual(name, chat_temp_dir(42, "2026-11-12T10:00:00Z"))
        self.assertNotEqual(name, chat_temp_dir(43, "2026-11-12T10:00:00Z"))
        # A cloned schema reuses ids for new chats; their creation time differs.
        self.assertNotEqual(name, chat_temp_dir(42, "2026-11-13T09:00:00Z"))
        self.assertEqual(chat_temp_dir(None), "")

    def test_subagent_gets_its_own_folder_inside_the_chats(self):
        chat = chat_temp_dir(42, "t")
        child = chat_temp_dir(42, "t", CHILD_RUN)
        self.assertEqual(child, f"{chat}/s-0f9e8d7c")
        self.assertEqual(child, chat_temp_dir(42, "t", CHILD_RUN))


class TempFrameTest(unittest.TestCase):
    def test_main_line_names_both_forms_and_subagent_folders(self):
        text = temp_frame("c-1a2b3c4d")
        self.assertIn("`/tmp/fuse/c-1a2b3c4d` on Linux and macOS", text)
        self.assertIn("`%TEMP%\\fuse\\c-1a2b3c4d` on Windows", text)
        self.assertIn("`$env:TEMP` can be a different folder", text)
        self.assertIn("Each subagent gets its own `s-…` folder", text)

    def test_child_line_points_at_the_chat_folder(self):
        text = temp_frame("c-1a2b3c4d/s-0f9e8d7c", subagent=True)
        self.assertIn("`/tmp/fuse/c-1a2b3c4d/s-0f9e8d7c`", text)
        self.assertIn("`%TEMP%\\fuse\\c-1a2b3c4d\\s-0f9e8d7c`", text)
        self.assertIn("The chat's folder `/tmp/fuse/c-1a2b3c4d` (Windows `%TEMP%\\fuse\\c-1a2b3c4d`)", text)
        self.assertIn("write only in your own folder", text)
        self.assertNotIn("Each subagent gets", text)

    def test_without_a_chat_it_falls_back_to_the_shared_dir(self):
        text = temp_frame("")
        self.assertIn("`/tmp/fuse` on Linux and macOS, `%TEMP%\\fuse` on Windows", text)
        self.assertNotIn("subagent", text)

    def test_prompt_states_it_last_after_memory_and_only_for_coding(self):
        base = dict(instructions="", profile="", thread_context="", tools=["web_search"])
        coding = build_system_prompt(**base, coding=True, temp_dir="c-1a2b3c4d", memory="full",
                                     memory_index="Global memories:\n- terse [feedback] — x")
        self.assertTrue(coding.endswith(temp_frame("c-1a2b3c4d")))
        self.assertLess(coding.index("Memory index:"), coding.index("Temp dir (`<tmp>`)"))
        # The static text before it is the same for every chat.
        other = build_system_prompt(**base, coding=True, temp_dir="c-99999999", memory="full",
                                    memory_index="Global memories:\n- terse [feedback] — x")
        self.assertEqual(coding[: coding.index("Temp dir (`<tmp>`)")], other[: other.index("Temp dir (`<tmp>`)")])
        self.assertNotIn("/tmp/fuse/", coding[: coding.index("Temp dir (`<tmp>`)")])
        self.assertIn("`<tmp>` in these examples is your temp dir", coding)
        self.assertNotIn("Temp dir (`<tmp>`)", build_system_prompt(**base))
        for kind in ("explore", "plan", ""):
            child = build_system_prompt(**base, coding=True, subagent=True, subagent_type=kind,
                                        temp_dir="c-1a2b3c4d/s-0f9e8d7c")
            self.assertTrue(child.endswith(temp_frame("c-1a2b3c4d/s-0f9e8d7c", subagent=True)), kind)


class InFuseTempTest(unittest.TestCase):
    def test_posix(self):
        for path in ("/tmp/fuse", "/tmp/fuse/c-1a2b3c4d", "/tmp/fuse/c-1/s-2/build", "/tmp//fuse/x/"):
            self.assertTrue(in_fuse_temp(path, False), path)
        for path in ("", "/tmp", "/tmp/fusex", "/tmp/other", "/tmp/fuse/../etc", "/root/fuse-dev", "~/tmp/fuse"):
            self.assertFalse(in_fuse_temp(path, False), path)

    def test_windows(self):
        for path in ("%TEMP%\\fuse", "%temp%/fuse/c-1", "~\\AppData\\Local\\Temp\\fuse\\c-1\\s-2",
                     "C:\\Users\\Ada\\AppData\\Local\\Temp\\fuse\\c-1", "C:\\Windows\\Temp\\fuse"):
            self.assertTrue(in_fuse_temp(path, True), path)
        for path in ("%TEMP%", "%TEMP%\\fusex", "~\\AppData\\Local\\Temp\\other", "C:\\fuse",
                     "~\\AppData\\Local\\Temp\\fuse\\..\\x", "C:\\Users\\Ada\\Documents\\fuse"):
            self.assertFalse(in_fuse_temp(path, True), path)


class EnsureFuseTempTest(unittest.TestCase):
    def ensure(self, cwd, windows=False, **mock):
        tool_exec = AsyncMock(**(mock or {"return_value": {"exit": 0}}))
        with patch.object(ws, "_tool_exec", tool_exec):
            asyncio.run(ws._ensure_fuse_temp(1, 2, cwd, windows=windows))
        return tool_exec

    def test_posix_cwd_in_temp_is_created(self):
        call = self.ensure("/tmp/fuse/c-1a2b3c4d/s-0f9e8d7c")
        call.assert_awaited_once_with(1, 2, "mkdir -p -- /tmp/fuse/c-1a2b3c4d/s-0f9e8d7c", "~", 8)

    def test_windows_cwd_is_created_where_file_tools_write(self):
        # _shell_cwd rewrites %TEMP% to the same ~\AppData\Local\Temp file tools use.
        cwd = ws._shell_cwd({"cwd": "%TEMP%\\fuse\\c-1a2b3c4d"}, "C:\\proj", windows=True)
        self.assertEqual(cwd, "~\\AppData\\Local\\Temp\\fuse\\c-1a2b3c4d")
        call = self.ensure(cwd, windows=True)
        call.assert_awaited_once_with(
            1, 2, "New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE "
            "'AppData\\Local\\Temp\\fuse\\c-1a2b3c4d') | Out-Null", "~", 8)

    def test_other_cwds_are_never_created(self):
        for cwd in ("/root/fuse-dev", "/tmp", "/tmp/other", "~"):
            self.ensure(cwd).assert_not_awaited()
        self.ensure("C:\\proj\\build", windows=True).assert_not_awaited()

    def test_failure_is_left_to_the_command(self):
        self.ensure("/tmp/fuse/c-1", side_effect=ws.WorkspaceError("host offline"))
        self.ensure("/tmp/fuse/c-1", side_effect=OSError("connection reset"))


if __name__ == "__main__":
    unittest.main()
