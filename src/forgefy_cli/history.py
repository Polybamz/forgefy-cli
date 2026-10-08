"""On-disk persistence for `forgefy chat` and `forgefy edit` sessions.

`forgefy chat` persists to a named session (default "default") unless
--no-history is passed, so it resumes where you left off like a normal chat
client. Agentic runs (tool-calling conversations) additionally store the full
message list, including tool calls and their results, under a parallel
`NAME.agent.json` file so a run can be resumed faithfully. Sessions can
contain source code and other workspace content pasted into the conversation,
so files are kept local only — never uploaded here — and permissioned the same
way as auth.py's credentials file.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MESSAGE_ROLES = {"system", "user", "assistant", "tool"}
_MESSAGE_KEYS = {"chat": "history", "agent": "messages"}


def history_dir() -> Path:
    return Path(
        os.environ.get("FORGEFY_HISTORY_DIR", str(Path.home() / ".forgefy" / "history"))
    ).expanduser()


def _validated_name(name: str) -> str:
    """Return `name` unchanged, or raise if it is not a safe session name.

    Factored out so chat (`session_path`) and agent (`agent_session_path`)
    files share exactly one validation rule and one error message.
    """
    if not _SESSION_NAME_RE.fullmatch(name):
        raise ValueError("Session name must be 1-64 letters, digits, underscores or hyphens.")
    return name


def session_path(name: str) -> Path:
    return history_dir() / f"{_validated_name(name)}.json"


def agent_session_path(name: str) -> Path:
    """Path of the full-message (tool-calling) session file for `name`."""
    return history_dir() / f"{_validated_name(name)}.agent.json"


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
    payload = {"history": [{"role": role, "content": content} for role, content in history]}
    _write_private_json(path, payload)


def _valid_message(message: object) -> bool:
    """True when `message` is a chat-API message we can persist and replay.

    Requires a dict with a known role and text-or-null content, and — when
    present — a list of tool calls (never inspected further here).
    """
    if not isinstance(message, dict):
        return False
    if message.get("role") not in _MESSAGE_ROLES:
        return False
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        return False
    return "tool_calls" not in message or isinstance(message["tool_calls"], list)


def _stored_messages(data: object) -> list | None:
    """The "messages" list from `data`, or None when the shape is unusable."""
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not all(_valid_message(item) for item in messages):
        return None
    return messages


def _write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — no-op on Windows, effective on POSIX
    except OSError:
        pass


def save_messages(name: str, messages: list[dict]) -> None:
    """Persist a full agent conversation (roles, tool calls and tool results).

    Validated up front so a bad entry raises before anything touches disk.
    """
    path = agent_session_path(name)
    if not isinstance(messages, list) or not all(_valid_message(item) for item in messages):
        raise ValueError(
            "Agent messages must be dicts with role system/user/assistant/tool, "
            "str or null content, and an optional list of tool_calls."
        )
    _write_private_json(path, {"messages": messages})


def load_messages(name: str) -> list[dict]:
    """Return the saved agent messages for `name`, or [] if none/unusable.

    Never raises: a missing, unreadable, corrupt or wrongly-shaped file — or
    any single invalid entry — degrades to an empty conversation.
    """
    try:
        path = agent_session_path(name)
    except ValueError:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
        return []
    messages = _stored_messages(data)
    return list(messages) if messages is not None else []


def _count_entries(path: Path, key: str) -> int:
    """Number of entries stored under `key`, or 0 when the file is unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
        return 0
    stored = data.get(key) if isinstance(data, dict) else None
    return len(stored) if isinstance(stored, list) else 0


def list_sessions() -> list[dict]:
    """Summarize every local session file, newest first (then name ascending).

    Each entry is {"name", "kind", "messages", "updated", "path"}. Corrupt
    files are still reported (with messages 0); files whose name is not a
    valid session name, or that cannot be stat'ed, are skipped entirely.
    """
    root = history_dir()
    if not root.is_dir():
        return []
    candidates: list[tuple[Path, str, str]] = []
    for path in root.glob("*.json"):
        if not path.name.endswith(".agent.json"):
            candidates.append((path, path.name[: -len(".json")], "chat"))
    for path in root.glob("*.agent.json"):
        candidates.append((path, path.name[: -len(".agent.json")], "agent"))
    entries: list[dict] = []
    for path, name, kind in candidates:
        if not _SESSION_NAME_RE.fullmatch(name):
            continue
        try:
            updated = path.stat().st_mtime
        except OSError:
            continue  # cannot stat at all — omit rather than report a phantom
        key = _MESSAGE_KEYS[kind]
        entries.append({
            "name": name,
            "kind": kind,
            "messages": _count_entries(path, key),
            "updated": float(updated),
            "path": str(path),
        })
    entries.sort(key=lambda entry: (-entry["updated"], entry["name"]))
    return entries


def delete_session(name: str, kind: str | None = None) -> bool:
    """Remove the chat file, the agent file, or both (kind None).

    Returns True if at least one existing file was removed; False when
    neither existed. Raises ValueError for an unknown kind or a bad name.
    """
    if kind not in (None, "chat", "agent"):
        raise ValueError("Session kind must be 'chat', 'agent' or None.")
    if kind is None:
        paths = [session_path(name), agent_session_path(name)]
    elif kind == "agent":
        paths = [agent_session_path(name)]
    else:
        paths = [session_path(name)]
    removed = False
    for path in paths:
        try:
            path.unlink()
            removed = True
        except OSError:
            pass  # missing, or not removable — treat as "nothing deleted"
    return removed


def clear_sessions() -> int:
    """Delete every file reported by `list_sessions`; return how many went."""
    removed = 0
    for entry in list_sessions():
        try:
            Path(entry["path"]).unlink()
            removed += 1
        except OSError:
            pass
    return removed
