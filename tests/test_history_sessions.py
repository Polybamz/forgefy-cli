import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forgefy_cli.history import (
    agent_session_path,
    clear_sessions,
    delete_session,
    list_sessions,
    load_messages,
    save_messages,
    save_session,
    session_path,
)

INVALID_NAMES = ("../escape", "has space", "", "a" * 65, "slash/here")


class HistorySessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self._env = patch.dict(os.environ, {"FORGEFY_HISTORY_DIR": str(self.root)})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _write_agent_raw(self, name, text):
        path = agent_session_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _touch(self, path, timestamp):
        os.utime(path, (timestamp, timestamp))

    # ---- save_messages / load_messages ---------------------------------

    def test_save_messages_round_trips(self):
        messages = [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        save_messages("default", messages)
        self.assertEqual(load_messages("default"), messages)

    def test_tool_role_and_tool_calls_round_trip_unchanged(self):
        messages = [
            {"role": "user", "content": "list files"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call1",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": '{"path": "."}'},
                    }
                ],
            },
            {"role": "tool", "content": "a.py\nb.py", "tool_call_id": "call1"},
        ]
        save_messages("agent", messages)
        self.assertEqual(load_messages("agent"), messages)

    def test_load_messages_missing_returns_empty(self):
        self.assertEqual(load_messages("default"), [])

    def test_load_messages_corrupt_returns_empty(self):
        self._write_agent_raw("default", "not json")
        self.assertEqual(load_messages("default"), [])

    def test_load_messages_wrong_shape_returns_empty(self):
        for text in ("[1, 2, 3]", '{"messages": "nope"}', "{}", '{"messages": [42]}',
                     '{"messages": [{"role": "nobody", "content": "x"}]}',
                     '{"messages": [{"role": "user", "content": 3}]}',
                     '{"messages": [{"role": "user", "content": "x", "tool_calls": "no"}]}'):
            with self.subTest(text=text):
                self._write_agent_raw("default", text)
                self.assertEqual(load_messages("default"), [])

    def test_save_messages_rejects_invalid_entries_without_writing(self):
        bad_messages = [
            [{"role": "nobody", "content": "x"}],
            [{"role": "user", "content": 3}],
            [{"role": "user", "content": "x", "tool_calls": "no"}],
            ["not-a-dict"],
        ]
        for messages in bad_messages:
            with self.subTest(messages=messages):
                with self.assertRaises(ValueError):
                    save_messages("default", messages)
                self.assertFalse(agent_session_path("default").exists())

    def test_save_messages_rejects_invalid_name(self):
        for bad in INVALID_NAMES:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                save_messages(bad, [{"role": "user", "content": "hi"}])

    # ---- session_path validation is unchanged --------------------------

    def test_session_path_still_rejects_invalid_names(self):
        for bad in INVALID_NAMES:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                session_path(bad)

    def test_agent_session_path_rejects_invalid_names(self):
        for bad in INVALID_NAMES:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                agent_session_path(bad)

    def test_session_path_error_message_is_stable(self):
        with self.assertRaises(ValueError) as caught:
            session_path("../escape")
        self.assertEqual(
            str(caught.exception),
            "Session name must be 1-64 letters, digits, underscores or hyphens.",
        )

    # ---- list_sessions -------------------------------------------------

    def test_list_sessions_reports_both_kinds(self):
        save_session("chatty", [("user", "one"), ("assistant", "two")])
        save_messages("agenty", [{"role": "user", "content": "hi"},
                                 {"role": "assistant", "content": "hello"},
                                 {"role": "tool", "content": "result"}])
        self._touch(session_path("chatty"), 1000.0)
        self._touch(agent_session_path("agenty"), 2000.0)

        entries = list_sessions()
        self.assertEqual([(e["name"], e["kind"], e["messages"]) for e in entries],
                         [("agenty", "agent", 3), ("chatty", "chat", 2)])
        self.assertEqual(entries[0]["path"], str(agent_session_path("agenty")))
        self.assertEqual(entries[1]["path"], str(session_path("chatty")))
        self.assertEqual(entries[0]["updated"], 2000.0)

    def test_list_sessions_missing_directory_is_empty(self):
        missing = self.root / "does-not-exist"
        with patch.dict(os.environ, {"FORGEFY_HISTORY_DIR": str(missing)}):
            self.assertFalse(missing.exists())
            self.assertEqual(list_sessions(), [])

    def test_list_sessions_sorts_newest_first_then_name(self):
        save_session("b", [("user", "one")])
        save_session("a", [("user", "two")])
        save_messages("c", [{"role": "user", "content": "three"}])
        self._touch(session_path("a"), 5000.0)
        self._touch(session_path("b"), 5000.0)
        self._touch(agent_session_path("c"), 5000.0)

        self.assertEqual([entry["name"] for entry in list_sessions()], ["a", "b", "c"])

    def test_list_sessions_reports_unparseable_file_as_zero(self):
        path = self._write_agent_raw("broken", "not json")
        self._touch(path, 1000.0)
        entries = list_sessions()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "broken")
        self.assertEqual(entries[0]["kind"], "agent")
        self.assertEqual(entries[0]["messages"], 0)

    def test_list_sessions_skips_files_with_invalid_names(self):
        save_session("good", [("user", "one")])
        (self.root / "has space.json").write_text('{"history": []}', encoding="utf-8")
        self.assertEqual([entry["name"] for entry in list_sessions()], ["good"])

    # ---- delete_session / clear_sessions -------------------------------

    def test_delete_session_removes_only_requested_kind(self):
        save_session("shared", [("user", "chat")])
        save_messages("shared", [{"role": "user", "content": "agent"}])

        self.assertTrue(delete_session("shared", "chat"))
        self.assertFalse(session_path("shared").exists())
        self.assertTrue(agent_session_path("shared").exists())

        self.assertTrue(delete_session("shared", "agent"))
        self.assertFalse(agent_session_path("shared").exists())

    def test_delete_session_without_kind_removes_both(self):
        save_session("shared", [("user", "chat")])
        save_messages("shared", [{"role": "user", "content": "agent"}])
        self.assertTrue(delete_session("shared"))
        self.assertEqual(list_sessions(), [])

    def test_delete_session_returns_false_when_nothing_existed(self):
        self.assertFalse(delete_session("ghost"))
        self.assertFalse(delete_session("ghost", "chat"))
        self.assertFalse(delete_session("ghost", "agent"))

    def test_delete_session_rejects_invalid_kind_and_name(self):
        with self.assertRaises(ValueError):
            delete_session("default", "everything")
        with self.assertRaises(ValueError):
            delete_session("../escape")

    def test_clear_sessions_returns_number_removed(self):
        save_session("one", [("user", "a")])
        save_session("two", [("user", "b")])
        save_messages("three", [{"role": "user", "content": "c"}])
        self.assertEqual(clear_sessions(), 3)
        self.assertEqual(list_sessions(), [])
        self.assertEqual(clear_sessions(), 0)

    def test_clear_sessions_without_directory_returns_zero(self):
        missing = self.root / "does-not-exist"
        with patch.dict(os.environ, {"FORGEFY_HISTORY_DIR": str(missing)}):
            self.assertEqual(clear_sessions(), 0)


if __name__ == "__main__":
    unittest.main()
