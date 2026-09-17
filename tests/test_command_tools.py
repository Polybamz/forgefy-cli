"""Unit and CLI-integration tests for opt-in command execution (--allow-commands)."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

from forgefy_cli.cli import main
from forgefy_cli.command_tools import CommandRunner

PY = json.dumps(sys.executable)  # quoted for embedding in a shell command line


class CommandRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def runner(self, approve=lambda display: True, timeout=10):
        return CommandRunner(self.root, approve, timeout)

    def test_runs_approved_command_and_captures_output(self):
        result = json.loads(self.runner().execute(json.dumps({
            "command": f"{sys.executable} -c \"print('hi')\""
        })))
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hi", result["stdout"])

    def test_nonzero_exit_code_is_reported_not_raised(self):
        result = json.loads(self.runner().execute(json.dumps({
            "command": f"{sys.executable} -c \"import sys; sys.exit(3)\""
        })))
        self.assertEqual(result["exit_code"], 3)

    def test_denied_command_does_not_run(self):
        marker = self.root / "marker.txt"
        runner = self.runner(approve=lambda display: False)
        result = json.loads(runner.execute(json.dumps({
            "command": f"{sys.executable} -c \"open('marker.txt','w').close()\""
        })))
        self.assertIn("declined", result["error"])
        self.assertFalse(marker.exists())
        self.assertEqual(runner.ran, [])

    def test_approval_shown_command_and_cwd(self):
        seen = {}
        def approve(display):
            seen["display"] = display
            return True
        self.runner(approve=approve).execute(json.dumps({"command": "true"}))
        self.assertIn("true", seen["display"])
        self.assertIn(str(self.root), seen["display"])

    def test_invalid_json_arguments_returns_error(self):
        result = json.loads(self.runner().execute("not json"))
        self.assertIn("error", result)

    def test_missing_command_key_returns_error(self):
        result = json.loads(self.runner().execute(json.dumps({"cmd": "true"})))
        self.assertIn("error", result)

    def test_empty_command_returns_error(self):
        result = json.loads(self.runner().execute(json.dumps({"command": "   "})))
        self.assertIn("nonempty", result["error"])

    def test_timeout_returns_error(self):
        result = json.loads(self.runner(timeout=1).execute(json.dumps({
            "command": f"{sys.executable} -c \"import time; time.sleep(5)\""
        })))
        self.assertIn("timed out", result["error"])

    def test_output_is_truncated(self):
        result = json.loads(self.runner().execute(json.dumps({
            "command": f"{sys.executable} -c \"print('x' * 40000)\""
        })))
        self.assertLess(len(result["stdout"]), 33000)
        self.assertIn("truncated", result["stdout"])


class EditCommandsIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.py").write_bytes(b"x = 1\n")

    def _run(self, extra_args, respond):
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch.dict(os.environ, {"FORGEFY_CONFIG": str(self.root / "config.toml")}), \
                patch("forgefy_cli.cli.httpx.Client", return_value=client), \
                patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", return_value="yes"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            code = main(["edit", "run the tests", "--provider", "ollama", "--model", "mock",
                         "--workspace", str(self.root), "--file", "main.py", *extra_args])
        return code, err.getvalue()

    def test_run_command_tool_offered_and_executed_when_allowed(self):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            turn = len(requests)
            if turn == 1:
                self.assertTrue(any(t["function"]["name"] == "run_command" for t in body["tools"]))
                args = {"command": f"{sys.executable} -c \"print('ok')\""}
                return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                    "content": None, "tool_calls": [{"id": "call1", "type": "function",
                    "function": {"name": "run_command", "arguments": json.dumps(args)}}]}}]})
            tool_result = json.loads(body["messages"][-1]["content"])
            self.assertEqual(tool_result["exit_code"], 0)
            self.assertIn("ok", tool_result["stdout"])
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Ran it."}}]})

        code, _ = self._run(["--allow-commands"], respond)
        self.assertEqual(code, 0)
        self.assertEqual(len(requests), 2)

    def test_run_command_not_offered_by_default(self):
        def respond(request):
            body = json.loads(request.content)
            names = {t["function"]["name"] for t in body["tools"]}
            self.assertNotIn("run_command", names)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "No commands available."}}]})

        code, _ = self._run([], respond)
        self.assertEqual(code, 0)

    def test_run_command_requested_without_flag_is_gracefully_rejected(self):
        # Defensive: even if a model calls run_command when it wasn't offered
        # (not every provider's model strictly honours the declared tool set),
        # nothing runs — FileTools.execute rejects the unknown name.
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                args = {"command": f"{sys.executable} -c \"open('should_not_exist.txt','w').close()\""}
                return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                    "content": None, "tool_calls": [{"id": "call1", "type": "function",
                    "function": {"name": "run_command", "arguments": json.dumps(args)}}]}}]})
            tool_result = json.loads(body["messages"][-1]["content"])
            self.assertIn("error", tool_result)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Can't do that."}}]})

        code, _ = self._run([], respond)
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "should_not_exist.txt").exists())


if __name__ == "__main__":
    unittest.main()
