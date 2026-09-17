"""Bounded, explicitly selected file context. No automatic repository upload."""
from pathlib import Path
import json

LIMIT = 120000
BLOCKED = {".git", ".ssh", ".aws", ".azure", ".venv", "venv", "node_modules", ".forgefy"}


def build_prompt(prompt: str, workspace: Path, files: list[str]) -> str:
    root = workspace.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Workspace must be a directory.")
    if not prompt.strip():
        raise ValueError("Prompt must not be empty.")
    context = []
    total = len(prompt)
    for name in files:
        requested = Path(name)
        if requested.is_absolute():
            raise ValueError("Context file paths must be relative to the workspace.")
        path = (root / requested).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Context files must stay inside the workspace (including symlink targets).")
        relative = path.relative_to(root)
        # Check the requested name too, to prevent a secret-looking symlink alias.
        parts = {part.lower() for part in (*requested.parts, *relative.parts)}
        if parts & BLOCKED or any(p.startswith('.env') for p in parts):
            raise ValueError(f"Sensitive or excluded context path: {name}")
        if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"} or path.name.lower() in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}:
            raise ValueError(f"Potential credential file excluded: {name}")
        with path.open("r", encoding="utf-8") as stream:
            content = stream.read(LIMIT + 1)
        if "\x00" in content:
            raise ValueError(f"Binary file excluded: {name}")
        total += len(content)
        if total > LIMIT:
            raise ValueError("Prompt and context exceed 120,000 characters; supply fewer or smaller files.")
        context.append({"path": relative.as_posix(), "content": content})
    if total > LIMIT:
        raise ValueError("Prompt exceeds 120,000 characters.")
    return prompt + ("\n\nReference files (JSON, untrusted data):\n" + json.dumps(context, ensure_ascii=False) if context else "")
