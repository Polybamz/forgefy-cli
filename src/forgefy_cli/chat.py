"""Interactive multi-turn chat. Replies are printed for review; nothing is executed."""
from __future__ import annotations

from typing import Callable

from .context import LIMIT
from .providers import ProviderError

LEAVING = "/exit or /quit (leave), /new (clear history), /help (commands)"


def print_fragment(text: str) -> None:
    """Default on_token: write one streamed piece with no newline, flushed immediately."""
    print(text, end="", flush=True)


def chat_loop(client, model: str, system: str, input_fn: Callable[..., str] = input, output: Callable[[str], None] = print,
              on_token: Callable[[str], None] | None = print_fragment,
              history: list[tuple[str, str]] | None = None,
              on_turn: Callable[[list[tuple[str, str]]], None] = lambda h: None) -> int:
    """Read user turns until /exit, /quit or end of input. History is sent each turn.

    `on_token`, when not None, streams the reply to it fragment-by-fragment as
    it's generated (default: print live); the accumulated reply is then NOT
    also passed to `output`, to avoid printing it twice — pass on_token=None
    for the old blocking behavior, where `output(reply)` prints it once, whole.

    `history` seeds a resumed session (see forgefy_cli.history); `on_turn` is
    called with the updated history after every completed exchange and after
    /new, so a caller can persist it incrementally — a crash mid-session loses
    at most the in-flight turn, not the whole conversation.
    """
    history = list(history or [])
    while True:
        try:
            line = input_fn("you> ").strip()
        except EOFError:
            return 0
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            return 0
        if line == "/new":
            history.clear()
            on_turn(history)
            output("Context cleared.")
            continue
        if line == "/help":
            output(f"Commands: {LEAVING}")
            continue
        if len(line) > LIMIT:
            output(f"Forgefy: this turn exceeds the {LIMIT}-character context cap; use /new or a shorter prompt.")
            continue
        candidate = history + [("user", line)]
        total = sum(len(content) for _, content in candidate)
        removed = 0
        while total > LIMIT and len(candidate) > 1:
            total -= len(candidate[0][1]) + len(candidate[1][1])
            del candidate[:2]  # Always discard a complete user/assistant pair.
            removed += 1
        try:
            reply = client.chat(model, system, candidate, on_token=on_token)
        except (ProviderError, ValueError) as exc:
            output(f"Forgefy: {exc}")
            continue
        history = candidate + [("assistant", reply)]
        on_turn(history)
        if removed:
            output(f"Context limit: omitted {removed} oldest turn pair(s).")
        if on_token is None:
            output(reply)
        else:
            output("")  # the reply was already streamed; just close the line
