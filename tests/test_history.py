import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forgefy_cli.history import history_dir, load_session, save_session, session_path


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self._env = patch.dict(os.environ, {"FORGEFY_HISTORY_DIR": str(self.root)})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_missing_session_returns_empty(self):
        self.assertEqual(load_session("default"), [])

    def test_save_then_load_round_trips(self):
        history = [("user", "hi"), ("assistant", "hello")]
        save_session("default", history)
        self.assertEqual(load_session("default"), history)

    def test_sessions_are_isolated_by_name(self):
        save_session("a", [("user", "one")])
        save_session("b", [("user", "two")])
        self.assertEqual(load_session("a"), [("user", "one")])
        self.assertEqual(load_session("b"), [("user", "two")])

    def test_overwrite_replaces_not_appends(self):
        save_session("default", [("user", "first")])
        save_session("default", [("user", "second")])
        self.assertEqual(load_session("default"), [("user", "second")])

    def test_corrupt_file_returns_empty(self):
        path = session_path("default")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(load_session("default"), [])

    def test_malformed_entries_are_skipped(self):
        path = session_path("default")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"history": [{"role": "user", "content": "ok"}, {"role": "system", "content": "bad-role"}, '
            '{"role": "assistant"}, "not-a-dict"]}',
            encoding="utf-8",
        )
        self.assertEqual(load_session("default"), [("user", "ok")])

    def test_invalid_session_name_rejected(self):
        for bad in ("../escape", "has space", "", "a" * 65, "slash/here"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                session_path(bad)

    def test_history_dir_defaults_under_home_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGEFY_HISTORY_DIR", None)
            self.assertEqual(history_dir(), Path.home() / ".forgefy" / "history")


if __name__ == "__main__":
    unittest.main()
