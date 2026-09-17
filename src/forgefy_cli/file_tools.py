"""Explicit-file tools. No shell execution; every change needs approval."""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable
import unicodedata

from .context import BLOCKED

FILE_LIMIT = 32000


def safe_display(text: str) -> str:
    """Do not let model/file text inject terminal control sequences into approvals."""
    return "".join(char if char in "\n\t" or not unicodedata.category(char).startswith("C")
                   else ascii(char)[1:-1] for char in text)


TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read an explicitly allowed UTF-8 file (maximum 32,000 bytes).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "replace_text", "description": "After read_file, replace one exact nonempty match. Requires user approval of the full diff.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
            "required": ["path", "old_text", "new_text"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "create_file", "description": "Create an explicitly allowed new UTF-8 file after user approval. Parent directory must already exist.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"], "additionalProperties": False},
    }},
]


class FileTools:
    def __init__(self, workspace: Path, files: list[str], approve: Callable[[str], bool],
                 create: list[str] | None = None) -> None:
        self.root = workspace.expanduser().resolve(strict=True)
        if not self.root.is_dir() or not files and not create:
            raise ValueError("A workspace directory and at least one existing --file or --create path is required.")
        self.allowed: set[str] = set()
        self.snapshots: dict[str, bytes] = {}
        self.approve = approve
        self.changed: list[str] = []
        self.creatable: list[str] = []
        for name in files:
            relative = self._relative(name)
            self._check_path(relative)
            self.allowed.add(relative.as_posix())
        for name in create or []:
            relative = self._relative(name)
            self.creatable.append(relative.as_posix())

    def _relative(self, name: str) -> Path:
        relative = Path(name)
        if not name or relative.is_absolute() or relative.anchor or ".." in relative.parts:
            raise ValueError("File paths must be relative and cannot contain '..'.")
        for part in relative.parts:
            stem = part.split('.')[0].upper()
            if (part.startswith('.') or part.lower() in BLOCKED or ':' in part
                    or part.endswith((' ', '.')) or stem in {'CON', 'PRN', 'AUX', 'NUL'}
                    or stem in {f'{prefix}{i}' for prefix in ('COM', 'LPT') for i in range(1, 10)}):
                raise ValueError("Hidden, excluded, or special file path rejected.")
        if relative.suffix.lower() in {'.pem', '.key', '.p12', '.pfx'} or relative.name.lower() in {
            'id_rsa', 'id_ed25519', 'credentials', 'credentials.json', 'secrets.json', 'secrets.toml',
        }:
            raise ValueError("Potential credential file rejected.")
        return relative

    def _check_path(self, relative: Path) -> Path:
        path = self.root
        for part in relative.parts:
            path = path / part
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError("Symlinks and junctions are not permitted for edit tools.")
        if not path.resolve(strict=False).is_relative_to(self.root):
            raise ValueError("File must stay in workspace.")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Only regular, non-hardlinked files are supported.")
        return path

    def _file(self, name: str) -> tuple[str, Path]:
        relative = self._relative(name)
        key = relative.as_posix()
        if key not in self.allowed and key not in self.creatable:
            raise ValueError("File not explicitly allowed by --file or --create.")
        path = self.root / relative
        if not path.resolve(strict=False).is_relative_to(self.root):
            raise ValueError("File must stay in workspace.")
        return key, path

    def execute(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments)
            expected = {'path'} if name == 'read_file' else {'path', 'content'} if name == 'create_file' else {'path', 'old_text', 'new_text'}
            if name not in {'read_file', 'replace_text', 'create_file'}:
                raise ValueError("Unknown tool; only read_file, replace_text and create_file are available.")
            if not isinstance(args, dict) or set(args) != expected or not all(isinstance(v, str) for v in args.values()):
                raise ValueError("Tool arguments do not match the schema.")
            key, path = self._file(args['path'])
            if name == 'read_file':
                data = self._read(path)
                self.snapshots[key] = data
                return json.dumps({'path': key, 'content': data.decode('utf-8')})
            if name == 'create_file':
                return self._create(key, path, args['content'])
            return self._replace(key, path, args['old_text'], args['new_text'])
        except (ValueError, OSError) as exc:
            return json.dumps({'error': str(exc)})
    @staticmethod
    def _read(path: Path) -> bytes:
        with path.open('rb') as stream:
            data = stream.read(FILE_LIMIT + 1)
        if len(data) > FILE_LIMIT or b'\x00' in data:
            raise ValueError("File is binary or exceeds the 32,000-byte cap.")
        data.decode('utf-8')
        return data

    def _create(self, key: str, path: Path, content: str) -> str:
        if len(content) > FILE_LIMIT:
            raise ValueError("New file content exceeds the 32,000-byte cap.")
        if '\x00' in content:
            raise ValueError("New file content is binary.")
        content.encode('utf-8')
        if path.exists():
            return json.dumps({'error': 'Cannot overwrite an existing file. Choose a different path.'})
        diff = '+' + content
        display = safe_display(diff)
        if len(display) > 16000:
            raise ValueError('File content too large to approve.')
        if not self.approve(display):
            return json.dumps({'error': 'The user declined this change; the file was not created.'})
        _, path = self._file(key)
        if path.exists():
            return json.dumps({'error': 'File appeared during approval; choose a different path.'})
        parent = path.parent
        if not parent.is_dir():
            raise ValueError('Parent directory does not exist; create it first.')
        self._atomic_write_create(path, content.encode('utf-8'))
        self.changed.append(key)
        return json.dumps({'status': 'created', 'path': key})

    @staticmethod
    def _atomic_write_create(path: Path, data: bytes) -> None:
        """Create a new file atomically via a sibling temp file, then swap."""
        handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix='.forgefy-', suffix='.tmp')
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _replace(self, key: str, path: Path, old_text: str, new_text: str) -> str:
        if key not in self.snapshots:
            return json.dumps({'error': 'read_file must be called for this file before replace_text.'})
        data = self._read(path)
        if data != self.snapshots[key]:
            del self.snapshots[key]
            return json.dumps({'error': 'File changed since read_file; read it again before editing.'})
        current = data.decode('utf-8')
        if not old_text:
            return json.dumps({'error': 'old_text must be nonempty.'})
        if old_text == new_text:
            return json.dumps({'error': 'Replacement produces no change.'})
        if current.count(old_text) != 1:
            return json.dumps({'error': 'old_text must match exactly once; read the file and include more surrounding lines.'})
        updated = current.replace(old_text, new_text, 1)
        if len(updated.encode('utf-8')) > FILE_LIMIT or '\x00' in updated:
            raise ValueError('Replacement is binary or exceeds the file size cap.')
        lines = difflib.unified_diff(
            current.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=f'a/{key}', tofile=f'b/{key}',
        )
        diff = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
        display = safe_display(diff)
        if len(display) > 16000:
            raise ValueError('Diff too large to approve; propose a smaller edit.')
        if not self.approve(display):
            return json.dumps({'error': 'The user declined this change; the file was not modified.'})
        _, path = self._file(key)
        if self._read(path) != data:
            self.snapshots.pop(key, None)
            raise ValueError('File changed during approval; read it again before editing.')
        self._atomic_write(path, updated.encode('utf-8'))
        self.snapshots[key] = updated.encode('utf-8')
        self.changed.append(key)
        return json.dumps({'status': 'applied', 'path': key, 'diff': diff})

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Write to a sibling temp file, then swap, so a crash never truncates the target."""
        mode = stat.S_IMODE(path.stat().st_mode)
        handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix='.forgefy-', suffix='.tmp')
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def summary(self) -> list[str]:
        """Files actually modified, in order, without duplicates."""
        return list(dict.fromkeys(self.changed))

