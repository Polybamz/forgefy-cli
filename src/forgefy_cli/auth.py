"""Local storage for the credential `forgefy login` obtains.

config.py is explicit that provider credentials are read only from the
environment and never stored in config.toml. This module doesn't relax that:
Provider.headers() still reads only os.environ. `forgefy login` just gives
one more, opt-in way to populate $FORGEFY_API_KEY for the process — from a
single-purpose file the user created by explicitly running `login`, kept out
of config.toml and permissioned as tightly as the OS allows, the same
convention `gh`/`docker`/`npm` use for their own login credential.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def credentials_path() -> Path:
    return Path(
        os.environ.get("FORGEFY_CREDENTIALS", str(Path.home() / ".forgefy" / "credentials.json"))
    ).expanduser()


def save_credentials(api_key: str) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"api_key": api_key}), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — no-op on Windows, effective on POSIX
    except OSError:
        pass  # best-effort; some filesystems (e.g. certain network mounts) reject chmod
    return path


def load_credentials() -> str | None:
    path = credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    key = data.get("api_key") if isinstance(data, dict) else None
    return key if isinstance(key, str) and key else None


def clear_credentials() -> bool:
    path = credentials_path()
    if path.exists():
        path.unlink()
        return True
    return False


def bootstrap_env() -> None:
    """Populate $FORGEFY_API_KEY from the stored credential if not already set.

    Called once at CLI startup, before providers are resolved. An explicitly
    exported FORGEFY_API_KEY always wins over a stored one.
    """
    if not os.environ.get("FORGEFY_API_KEY"):
        key = load_credentials()
        if key:
            os.environ["FORGEFY_API_KEY"] = key
