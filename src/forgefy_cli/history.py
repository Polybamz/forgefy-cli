"""On-disk persistence for `forgefy chat` sessions.

`forgefy chat` persists to a named session (default "default") unless
--no-history is passed, so it resumes where you left off like a normal chat
client. Sessions can contain source code and other workspace content pasted
into the conversation, so files are kept local only — never uploaded here —
and permissioned the same way as auth.py's credentials file.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def history_dir() -> Path:
    return Path(
        os.environ.get("FORGEFY_HISTORY_DIR", str(Path.home() / ".forgefy" / "history"))
    ).expanduser()


def session_path(name: str) -> Path:
    if not _SESSION_NAME_RE.fullmatch(name):
        raise ValueError("Session name must be 1-64 letters, digits, underscores or hyphens.")
    return history_dir() / f"{name}.json"


def load_session(name: str) -> list[tuple[str, str]]:
    """Return the saved (role, content) turns for `name`, or [] if none/unreadable."""
    path = session_path(name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    turns = data.get("history") if isinstance(data, dict) else None
    if not isinstance(turns, list):
        return []
    result: list[tuple[str, str]] = []
    for turn in turns:
        if (
            isinstance(turn, dict)
            and turn.get("role") in {"user", "assistant"}
            and isinstance(turn.get("content"), str)
        ):
            result.append((turn["role"], turn["content"]))
    return result


def save_session(name: str, history: list[tuple[str, str]]) -> None:
    path = session_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"history": [{"role": role, "content": content} for role, content in history]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — no-op on Windows, effective on POSIX
    except OSError:
        pass
