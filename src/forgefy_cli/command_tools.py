"""Explicit, approval-gated shell command execution.

Opt-in only: `forgefy edit --allow-commands`. Kept in its own module, separate
from file_tools.py (whose docstring promises no shell execution) — that
promise stays true by default; this is what a caller turns on deliberately.

Not a sandbox. A command approved here runs with the user's full OS
privileges, filesystem access, and network — the only safety boundary is the
same one file edits already use: the model proposes an exact command, the
user sees it and must approve it before anything runs.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

# Matches file_tools.FILE_LIMIT — keeps a tool result within the CLI's
# character-budget context cap regardless of how chatty a command's output is.
OUTPUT_LIMIT = 32000
DEFAULT_TIMEOUT = 120

RUN_COMMAND_TOOL = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "Run one exact shell command line in the workspace root, after user approval. "
            "Use to build, lint, or run tests on files you've edited. Runs with the user's "
            "full privileges and network access — it is not sandboxed."
        ),
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}


def _truncate(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    omitted = len(text) - OUTPUT_LIMIT
    return text[:OUTPUT_LIMIT] + f"\n...[truncated, {omitted} more characters]"


class CommandRunner:
    def __init__(self, workspace: Path, approve: Callable[[str], bool], timeout: int = DEFAULT_TIMEOUT) -> None:
        self.root = workspace
        self.approve = approve
        self.timeout = timeout
        self.ran: list[str] = []

    def execute(self, arguments: str) -> str:
        try:
            args = json.loads(arguments)
            if not isinstance(args, dict) or set(args) != {"command"} or not isinstance(args["command"], str):
                raise ValueError("Tool arguments do not match the schema.")
            command = args["command"].strip()
            if not command:
                raise ValueError("command must be nonempty.")
        except (ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)})

        if not self.approve(f"$ {command}\n(cwd: {self.root})"):
            return json.dumps({"error": "The user declined to run this command; it did not run."})

        try:
            result = subprocess.run(
                command,
                shell=True,  # the command is one already-approved string, not argv — approval IS the trust boundary
                cwd=self.root,
                capture_output=True,
                timeout=self.timeout,
                text=True,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Command timed out after {self.timeout}s."})
        except OSError as exc:
            return json.dumps({"error": f"Could not run command: {exc}"})

        self.ran.append(command)
        return json.dumps({
            "exit_code": result.returncode,
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr),
        })
