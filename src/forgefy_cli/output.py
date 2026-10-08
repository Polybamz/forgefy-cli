"""Terminal output: plain text, or newline-delimited JSON for scripting.

`forgefy --json` mirrors Cline CLI's headless contract: every message is one
JSON object on its own stdout line, so a pipeline can consume it with `jq` or a
line reader, while the exit codes stay exactly the same as in text mode. In
text mode the split is the one this CLI already used: assistant replies go to
stdout (so they can be piped), and status, errors and approval prompts go to
stderr.

Streams are resolved lazily on every write rather than captured at construction
time, so a Reporter created inside `main()` still honours
`contextlib.redirect_stdout`/`redirect_stderr` — the behaviour the test suite
relies on.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable, TextIO


class Reporter:
    """Writes assistant text, status lines, errors and approval prompts.

    JSON mode shape (matching Cline CLI's documented fields):
      {"type": "say", "say": "text",  "text": ..., "ts": ...}   assistant reply
      {"type": "say", "say": "text",  "text": ..., "partial": true, "ts": ...} streamed chunk
      {"type": "say", "say": "info",  "text": ..., "ts": ...}   status/diagnostics
      {"type": "say", "say": "error", "text": ..., "ts": ...}   error
      {"type": "ask", "ask": "approval", "text": ..., "ts": ...} approval prompt
    """

    def __init__(self, json_mode: bool = False, stdout: TextIO | None = None,
                 stderr: TextIO | None = None, clock: Callable[[], float] = time.time) -> None:
        self.json_mode = json_mode
        self._stdout = stdout
        self._stderr = stderr
        self._clock = clock
        self._open_line = False

    @property
    def stdout(self) -> TextIO:
        return self._stdout if self._stdout is not None else sys.stdout

    @property
    def stderr(self) -> TextIO:
        return self._stderr if self._stderr is not None else sys.stderr

    def _emit_json(self, payload: dict[str, Any]) -> None:
        payload.setdefault('ts', int(self._clock() * 1000))
        self._end_line()
        self.stdout.write(json.dumps(payload, ensure_ascii=False) + '\n')
        self.stdout.flush()

    def _end_line(self) -> None:
        """Close a text-mode line left open by a streamed reply."""
        if self._open_line:
            self.stdout.write('\n')
            self.stdout.flush()
            self._open_line = False

    def say(self, text: str) -> None:
        """A complete assistant reply."""
        if self.json_mode:
            self._emit_json({'type': 'say', 'say': 'text', 'text': text})
        else:
            self._end_line()
            print(text, file=self.stdout)

    def stream(self, chunk: str) -> None:
        """One streamed fragment of the reply that is still being generated."""
        if self.json_mode:
            self._emit_json({'type': 'say', 'say': 'text', 'text': chunk, 'partial': True})
            return
        self.stdout.write(chunk)
        self.stdout.flush()
        self._open_line = True

    def close_stream(self) -> None:
        self._end_line()

    def event(self, subtype: str, text: str, **fields: Any) -> None:
        """A status line that also carries structured data for --json consumers.

        Text mode prints `text` to stdout exactly like say(); JSON mode emits
        the same fields plus every extra keyword, so `forgefy history --json`
        can be parsed without a second format. `subtype` becomes the `say` type.
        """
        if self.json_mode:
            self._emit_json({'type': 'say', 'say': subtype, 'text': text, **fields})
        else:
            self._end_line()
            print(text, file=self.stdout)

    def info(self, text: str) -> None:
        """Status/diagnostic line: never part of the reply itself."""
        if self.json_mode:
            self._emit_json({'type': 'say', 'say': 'info', 'text': text})
        else:
            self._end_line()
            print(text, file=self.stderr)

    def error(self, text: str) -> None:
        if self.json_mode:
            self._emit_json({'type': 'say', 'say': 'error', 'text': text})
        else:
            self._end_line()
            print(text, file=self.stderr)

    def ask(self, text: str) -> None:
        """The body of an approval prompt, shown before reading the answer."""
        if self.json_mode:
            self._emit_json({'type': 'ask', 'ask': 'approval', 'text': text})
        else:
            self._end_line()
            print(text, file=self.stderr)
