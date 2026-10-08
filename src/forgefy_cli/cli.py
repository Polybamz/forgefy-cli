"""Forgefy command-line interface — Cline-style surface.

    forgefy                       interactive agent session
    forgefy "task"                one act-mode task; every change needs approval
    forgefy -p "task"             plan mode: read-only, no edits or commands
    forgefy --json "task"         newline-delimited JSON for scripts and CI
    forgefy <command>             auth, config, doctor, history, models,
                                  providers, skills, update, version

The older run/chat/edit commands stay available for scripts written against
earlier releases; see README.md for the mapping and the deliberate differences.
"""
from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import sys
import time

import httpx

from .auth import bootstrap_env
from .chat import chat_loop
from .config import Provider, TEMPLATE, config_path, load_config
from .context import build_prompt
from .editing import AgentOptions, ToolSession, agent_repl, edit_files, prompt_note
from .history import (agent_session_path, clear_sessions, delete_session, history_dir,
                      list_sessions, load_messages, load_session, save_messages, save_session,
                      session_path)
from .login import device_login, logout
from .output import Reporter
from .providers import ModelClient, ProviderError
from .release import compare, installed_version, latest_version
from .skills import SKILLS, system_prompt

try:
    _VERSION = version("forgefy-cli")
except PackageNotFoundError:  # running from source without an install record
    _VERSION = "0.0.0-dev"

# --key populates this variable for this process only. Provider credentials are
# never read from configuration or from disk, so an override cannot outlive the run.
_KEY_OVERRIDE_ENV = "FORGEFY_CLI_KEY_OVERRIDE"
DEFAULT_TIMEOUT = 120
AGENT_COMMANDS = {"run", "chat", "edit", "agent"}


def _opt(args: argparse.Namespace, name: str, default=None):
    """Read an option that may have been given before or after the subcommand."""
    return getattr(args, name, default)


def _clip(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[:limit] + f'... [{len(text) - limit} more characters]'


def _normalize(command: str | None) -> str:
    return {'h': 'history', 'login': 'auth'}.get(command or 'agent', command or 'agent')


# Options that consume the next token, so the bare-prompt scan can skip their values.
VALUE_OPTIONS = {
    '-m', '--model', '-P', '--provider', '-c', '--cwd', '--workspace', '--config', '--data-dir',
    '-k', '--key', '-s', '--system', '-t', '--timeout', '--auto-approve', '--id', '--max-turns',
    '--command-timeout', '--file', '--create', '--skill', '--skill-file',
    '--limit', '--show', '--delete',
}
HELP_OPTIONS = {'-h', '--help', '-V', '--version'}


def _command_names(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:  # noqa: SLF001 — argparse exposes no public accessor
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return set(action.choices)
    return set()


def _prepare_argv(argv: list[str], command_names: set[str]) -> list[str]:
    """Turn the Cline-style `forgefy "task"` form into the implicit `agent` command.

    argparse cannot combine an optional positional with subcommands — the first
    token would always be taken as the prompt — so the first token that is
    neither an option nor a known command is treated as the task, the way
    `cline "task"` works. `forgefy -- "task"` forces the task form when the task
    itself would otherwise look like a flag.
    """
    tokens = list(argv)
    if tokens[:1] == ['--']:
        return ['agent', *tokens]
    index, prompt = 0, None
    while index < len(tokens):
        token = tokens[index]
        if token == '--':
            prompt = tokens[index + 1] if index + 1 < len(tokens) else ''
            break
        if not token.startswith('-'):
            prompt = token
            break
        index += 2 if token in VALUE_OPTIONS else 1
    if prompt is not None:
        return tokens if prompt in command_names else ['agent', *tokens]
    # No prompt at all: bare `forgefy`, or flags such as -p/-i/--json, start a session —
    # but `forgefy --help`/`--version` must still reach the top-level parser.
    return tokens if any(token in HELP_OPTIONS for token in tokens) else ['agent', *tokens]


def _common_options() -> argparse.ArgumentParser:
    """Flags accepted on either side of the subcommand, like Cline CLI's globals.

    Every option defaults to SUPPRESS so a flag given *before* the subcommand is
    not reset by that subparser's default; unresolved flags are read with _opt().
    """
    common = argparse.ArgumentParser(add_help=False)

    def option(*names: str, **kwargs) -> None:
        kwargs.setdefault("default", argparse.SUPPRESS)
        common.add_argument(*names, **kwargs)

    option("-m", "--model", help="Exact provider model ID; no automatic paid fallback")
    option("-P", "--provider", help="Provider profile name (default: config, else ollama)")
    option("-c", "--cwd", "--workspace", dest="cwd", type=Path, metavar="PATH",
           help="Working directory (default: the current directory)")
    option("--json", action="store_true", help="Emit newline-delimited JSON messages instead of text")
    option("--auto-approve", choices=("true", "false"), metavar="BOOL",
           help="Auto-approve file changes and commands without asking (default: false)")
    option("-t", "--timeout", type=int, metavar="SECONDS",
           help=f"Timeout for model requests and commands (default: {DEFAULT_TIMEOUT})")
    option("--config", type=Path, metavar="PATH",
           help="Config file (*.toml), or a directory holding config.toml")
    option("--data-dir", type=Path, metavar="PATH",
           help="Keep config, credentials and session history under PATH (like CLINE_DATA_DIR)")
    option("-k", "--key", dest="api_key", metavar="API-KEY", help="API key override for this run only")
    option("-s", "--system", metavar="PROMPT", help="Replace the built-in system prompt")
    option("-v", "--verbose", action="store_true", help="Print extra diagnostics to stderr")
    return common


def _auth_parser(sub, common, name: str, help_text: str, aliases: list[str] | None = None):
    auth = sub.add_parser(name, parents=[common], help=help_text, aliases=aliases or [])
    auth.add_argument("provider", nargs="?",
                      help="Provider profile to check its key for (default: your Forgefy account)")
    auth.add_argument("--api-url", help="Override FORGEFY_API_URL for this sign-in only")
    return auth


def parser() -> argparse.ArgumentParser:
    common = _common_options()
    result = argparse.ArgumentParser(
        prog="forgefy", parents=[common],
        description="Forgefy: an agent in your terminal. `forgefy` starts an interactive session, "
                    "`forgefy \"task\"` runs one task with every change approved, and "
                    "`forgefy -p \"task\"` plans without changing anything.",
        epilog="Examples: forgefy \"add tests for auth.py\" | forgefy -p \"plan it first\" | "
               "forgefy --json \"list TODO comments\" | forgefy -- \"a task that looks like a flag\"")
    result.add_argument("-V", "--version", action="version", version=f"Forgefy CLI {_VERSION}")
    sub = result.add_subparsers(dest="command")
    # The implicit default command: `forgefy "task"` and bare `forgefy` both land here.
    agent = sub.add_parser("agent", parents=[common],
                           help="Run one task, or start an interactive session (the default command)")
    agent.add_argument("prompt", nargs="?",
                       help="Task for the agent; omit it to start an interactive session")
    agent.add_argument("-p", "--plan", action="store_true",
                       help="Plan mode: read-only tools, no edits and no commands")
    agent.add_argument("-i", "--tui", action="store_true",
                       help="Start the interactive session (what omitting the prompt does)")
    agent.add_argument("--id", dest="session_id", metavar="SESSION-ID", help="Resume a saved session by name")
    agent.add_argument("--no-history", action="store_true", help="Don't load or save session history")
    agent.add_argument("--max-turns", type=int, default=12, help="Maximum model requests per turn (1-30; default 12)")
    agent.add_argument("--allow-commands", action="store_true",
                       help="Let the agent propose shell commands (each still needs approval). Not sandboxed.")
    agent.add_argument("--command-timeout", type=int, metavar="SECONDS",
                       help=f"Seconds before an approved command is killed (default: {DEFAULT_TIMEOUT})")
    agent.add_argument("--file", action="append", default=[],
                       help="Restrict reads and edits to this workspace-relative file; repeatable")
    agent.add_argument("--create", action="append", default=[],
                       help="Restrict file creation to this new relative path; repeatable")
    agent.add_argument("--skill", choices=sorted(SKILLS), default="code")
    agent.add_argument("--skill-file", type=Path, action="append", default=[],
                       help="Trusted Markdown instructions to include; repeatable")
    _auth_parser(sub, common, "auth", "Sign in to your Forgefy account, or check a provider's key")
    _auth_parser(sub, common, "login", "Alias for auth", aliases=["signin"])
    sub.add_parser("logout", parents=[common], help="Remove the locally stored Forgefy account credential")
    config = sub.add_parser("config", parents=[common], help="Display config path, or create a starter file")
    config.add_argument("--init", action="store_true", help="Create a starter config; never overwrite")
    sub.add_parser("doctor", parents=[common], help="Check configuration, keys and session state")
    history = sub.add_parser("history", parents=[common], aliases=["h"],
                             help="List session history or manage a saved session")
    history.add_argument("--limit", type=int, default=20, help="How many recent sessions to list (default 20)")
    history.add_argument("--show", metavar="SESSION-ID", help="Print a saved session's messages")
    history.add_argument("--delete", metavar="SESSION-ID", help="Delete a saved session")
    history.add_argument("--clear", action="store_true", help="Delete every saved session")
    sub.add_parser("models", parents=[common], help="List live provider model IDs (availability and pricing vary)")
    sub.add_parser("providers", parents=[common], help="List built-in and configured provider profiles")
    sub.add_parser("skills", parents=[common], help="List built-in coding skills")
    sub.add_parser("version", parents=[common], help="Show the CLI version")
    sub.add_parser("update", parents=[common], help="Check PyPI for a newer release (does not install it)")
    run = sub.add_parser("run", parents=[common], help="One-shot suggestion: send a prompt, print the reply")
    run.add_argument("prompt", help="Coding request; use '-' to read from stdin")
    run.add_argument("--file", action="append", default=[],
                     help="Explicit relative file to send; repeatable. Review for secrets first.")
    run.add_argument("--skill", choices=sorted(SKILLS), default="code")
    run.add_argument("--skill-file", type=Path, action="append", default=[])
    run.add_argument("--no-stream", action="store_true",
                     help="Wait for the full response instead of printing it as it streams")
    chat = sub.add_parser("chat", parents=[common], help="Interactive chat without workspace tools")
    chat.add_argument("--skill", choices=sorted(SKILLS), default="code")
    chat.add_argument("--skill-file", type=Path, action="append", default=[])
    chat.add_argument("--no-stream", action="store_true",
                      help="Wait for each full response instead of printing it as it streams")
    chat.add_argument("--session", default="default", help="Named session to save to disk (default: 'default')")
    chat.add_argument("--resume", action="store_true",
                      help="Load previous turns from --session first; without this every run starts fresh")
    chat.add_argument("--no-history", action="store_true", help="Don't load or save this session at all")
    edit = sub.add_parser("edit", parents=[common],
                          help="Edit explicitly listed files; every change needs approval")
    edit.add_argument("prompt", help="Requested change")
    edit.add_argument("--file", action="append", default=[], help="Allowed existing relative file; repeatable")
    edit.add_argument("--create", action="append", default=[],
                      help="Approved relative path to create; repeatable, parent dir must exist")
    edit.add_argument("--max-turns", type=int, default=12, help="Maximum model requests (1-30; default 12)")
    edit.add_argument("--allow-commands", action="store_true",
                      help="Let the model propose shell commands; each still requires your approval. Not sandboxed.")
    edit.add_argument("--command-timeout", type=int, metavar="SECONDS",
                      help=f"Seconds before an approved command is killed (default: {DEFAULT_TIMEOUT})")
    return result


def _apply_env_overrides(args: argparse.Namespace) -> None:
    """--data-dir/--config must reach the environment before anything is loaded."""
    data_dir = _opt(args, "data_dir", None)
    if data_dir is not None:
        base = Path(data_dir).expanduser()
        os.environ["FORGEFY_CONFIG"] = str(base / "config.toml")
        os.environ["FORGEFY_CREDENTIALS"] = str(base / "credentials.json")
        os.environ["FORGEFY_HISTORY_DIR"] = str(base / "history")
    config = _opt(args, "config", None)
    if config is not None:
        path = Path(config).expanduser()
        # A *.toml path is the config file itself; anything else is a config directory.
        os.environ["FORGEFY_CONFIG"] = str(path if path.suffix.lower() == ".toml" else path / "config.toml")


def _apply_key(provider: Provider, key: str | None) -> Provider:
    """--key sends a key for this process only; nothing is written to disk."""
    if not key:
        return provider
    os.environ[_KEY_OVERRIDE_ENV] = key
    return Provider(provider.name, provider.base_url, _KEY_OVERRIDE_ENV)


def main(argv: list[str] | None = None) -> int:
    parser_ = parser()
    tokens = sys.argv[1:] if argv is None else list(argv)
    args = parser_.parse_args(_prepare_argv(tokens, _command_names(parser_)))
    reporter = Reporter(json_mode=bool(_opt(args, "json", False)))
    try:
        _apply_env_overrides(args)
        bootstrap_env()
        return _dispatch(args, reporter)
    except (ValueError, OSError, ProviderError) as exc:
        reporter.error(f"Forgefy: {exc}")
        return 1
    except KeyboardInterrupt:
        reporter.error("\nCancelled.")
        return 130
    finally:
        reporter.close_stream()


def _dispatch(args: argparse.Namespace, reporter: Reporter) -> int:
    command = _normalize(args.command)
    if command == "version":
        reporter.say(f"Forgefy CLI {_VERSION}")
        return 0
    if command == "update":
        return _update(reporter)
    if command == "skills":
        for name, description in SKILLS.items():
            reporter.say(f"{name}: {description}")
        return 0
    if command == "config":
        path = config_path()
        if args.init:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                stream.write(TEMPLATE)
        reporter.say(str(path))
        return 0
    if command == "logout":
        return logout(reporter.info)
    if command == "history":
        return _history(args, reporter)
    settings, providers = load_config()
    if command == "auth":
        return _auth(args, reporter, providers)
    if command in {"providers", "doctor"}:
        return _providers(reporter, command, providers)
    name, provider = _resolve_provider(args, settings, providers)
    provider = _apply_key(provider, _opt(args, "api_key", None))
    model = _resolve_model(args, settings, name) if command in AGENT_COMMANDS else None
    if _opt(args, "verbose", False):
        reporter.info(f"provider={name} base_url={provider.base_url} model={model or '-'} "
                      f"config={config_path()} sessions={history_dir()}")
    timeout = _opt(args, "timeout", None) or DEFAULT_TIMEOUT
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=10)) as http:
        client = ModelClient(provider, http)
        if command == "models":
            for model_id in client.models():
                reporter.say(model_id)
            return 0
        if command == "run":
            return _run(args, reporter, client, name, model)
        if command == "chat":
            return _chat(args, reporter, client, name, model)
        if command == "edit":
            return _edit(args, reporter, client, model)
        return _agent(args, reporter, client, name, model)


def _providers(reporter: Reporter, command: str, providers: dict[str, Provider]) -> int:
    for name, provider in providers.items():
        if provider.api_key_env:
            status = f"{provider.api_key_env}: {'set' if os.environ.get(provider.api_key_env) else 'missing'}"
        else:
            status = "no key required"
        reporter.say(f"{name}\t{provider.base_url}\t{status}")
    if command == "doctor":
        reporter.say(f"Config: {config_path()}")
        reporter.say(f"Sessions: {history_dir()}")
        reporter.say("Network/model availability not checked. Use `forgefy models`.")
    return 0


def _resolve_provider(args: argparse.Namespace, settings: dict,
                      providers: dict[str, Provider]) -> tuple[str, Provider]:
    name = _opt(args, "provider", None) or settings.get("default_provider", "ollama")
    if name not in providers:
        raise ValueError(f"Unknown provider '{name}'. Run forgefy providers.")
    return name, providers[name]


def _resolve_model(args: argparse.Namespace, settings: dict, name: str) -> str:
    # A default model belongs to its configured provider, not to an override.
    default = settings.get("default_provider", "ollama")
    model = _opt(args, "model", None) or (settings.get("default_model") if name == default else None)
    if not model or not model.strip():
        raise ValueError("Choose a model with -m/--model (see forgefy models), or set default_model in config.")
    return model


def _workspace(args: argparse.Namespace) -> Path:
    return _opt(args, "cwd", None) or Path.cwd()


def _system_prompt(args: argparse.Namespace) -> str:
    return _opt(args, "system", None) or system_prompt(_opt(args, "skill", "code"),
                                                      list(_opt(args, "skill_file", []) or []))


def _run(args: argparse.Namespace, reporter: Reporter, client: ModelClient,
         provider_name: str, model: str) -> int:
    prompt = sys.stdin.read(120001) if args.prompt == "-" else args.prompt
    prompt = build_prompt(prompt, _workspace(args), list(_opt(args, "file", []) or []))
    reporter.info(f"Sending request to {provider_name} / {model}. Provider pricing applies; no fallback.")
    if reporter.json_mode or _opt(args, "no_stream", False):
        reporter.say(client.complete(model, _system_prompt(args), prompt))
    else:
        client.complete(model, _system_prompt(args), prompt, on_token=reporter.stream)
        reporter.close_stream()
    return 0


def _chat(args: argparse.Namespace, reporter: Reporter, client: ModelClient,
          provider_name: str, model: str) -> int:
    session = _opt(args, "session_id", None) or _opt(args, "session", "default") or "default"
    resume = bool(_opt(args, "session_id", None)) or bool(_opt(args, "resume", False))
    no_history = bool(_opt(args, "no_history", False))
    reporter.info(f"Chatting with {provider_name} / {model}. /exit to leave; "
                  "replies are suggestions to review, never executed.")
    if no_history:
        initial_history, on_turn = [], (lambda h: None)
    else:
        initial_history = load_session(session) if resume else []
        on_turn = lambda h: save_session(session, h)  # noqa: E731
        if resume and initial_history:
            reporter.info(f"Resumed session '{session}' ({len(initial_history) // 2} previous turn(s)). "
                          "/new to start fresh.")
        elif resume:
            reporter.info(f"No previous history for session '{session}' — starting fresh.")
        else:
            reporter.info(f"Session '{session}' will be saved to {session_path(session)}. "
                          "Use --resume to continue it next time.")
    on_token = None if (reporter.json_mode or _opt(args, "no_stream", False)) else reporter.stream
    return chat_loop(client, model, _system_prompt(args), on_token=on_token,
                     history=initial_history, on_turn=on_turn, output=reporter.say)


def _edit(args: argparse.Namespace, reporter: Reporter, client: ModelClient, model: str) -> int:
    """The pre-0.4 `edit` command: explicit --file/--create only, one prompt."""
    auto_approve = _opt(args, "auto_approve", "false") == "true"
    timeout = _opt(args, "command_timeout", None) or _opt(args, "timeout", None) or DEFAULT_TIMEOUT
    reporter.info(f"Editing with {client.provider.name} / {model}. Selected files are sent to this provider; "
                  "pricing applies, there is no fallback, and applied edits are not rolled back for you.")
    return edit_files(client, model, args.prompt, _workspace(args), list(args.file),
                      _opt(args, "max_turns", 12), list(args.create),
                      bool(_opt(args, "allow_commands", False)), timeout,
                      reporter=reporter, auto_approve=auto_approve)


def _agent(args: argparse.Namespace, reporter: Reporter, client: ModelClient,
           provider_name: str, model: str) -> int:
    """The Cline-style default: the agent reads the workspace and proposes the rest."""
    plan = bool(_opt(args, "plan", False))
    auto_approve = _opt(args, "auto_approve", "false") == "true"
    options = AgentOptions(
        workspace=_workspace(args),
        files=list(_opt(args, "file", []) or []),
        create=list(_opt(args, "create", []) or []),
        scope="workspace",
        max_turns=_opt(args, "max_turns", 12),
        allow_commands=bool(_opt(args, "allow_commands", False)) and not plan,
        command_timeout=_opt(args, "command_timeout", None) or _opt(args, "timeout", None) or DEFAULT_TIMEOUT,
        plan=plan,
        auto_approve=auto_approve,
        skill=_opt(args, "skill", "code"),
        skill_files=[Path(p) for p in (_opt(args, "skill_file", []) or [])],
        system=_opt(args, "system", None))
    session_id = _opt(args, "session_id", None)
    no_history = bool(_opt(args, "no_history", False))
    session_name = session_id or "default"
    reporter.info(f"forgefy {provider_name} / {model}. Workspace files the agent reads are sent to this "
                  "provider; pricing applies and there is no fallback.")
    reporter.info(prompt_note(options))
    if not auto_approve and not sys.stdin.isatty():
        reporter.info("Non-interactive session: file changes and commands are declined unless you pass "
                      "--auto-approve true.")
    messages = load_messages(session_id) if session_id and not no_history else None
    if messages:
        reporter.info(f"Resumed agent session '{session_id}' ({len(messages)} stored message(s)).")
    if no_history:
        on_turn = None
    else:
        on_turn = lambda stored: save_messages(session_name, stored)  # noqa: E731
        reporter.info(f"Session '{session_name}' is saved to {agent_session_path(session_name)}.")
    session = ToolSession(client, model, options, reporter, on_turn=on_turn, messages=messages)
    if args.prompt is not None:
        session.run(args.prompt)
        if not bool(_opt(args, "tui", False)):
            return 0
    return agent_repl(client, model, options, reporter, session=session)


def _history(args: argparse.Namespace, reporter: Reporter) -> int:
    if args.clear:
        reporter.info(f"Removed {clear_sessions()} session file(s) from {history_dir()}.")
        return 0
    if args.delete:
        removed = delete_session(args.delete)
        reporter.info(f"{'Removed' if removed else 'No session named'} '{args.delete}'.")
        return 0 if removed else 1
    if args.show:
        return _show_session(args.show, reporter)
    rows = list_sessions()[: max(1, args.limit)]
    if not rows:
        reporter.info(f"No saved sessions in {history_dir()}.")
        return 0
    for row in rows:
        reporter.event("history",
                       f"{row['name']}\t{row['kind']}\t{row['messages']}\t"
                       f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(row['updated']))}",
                       name=row["name"], kind=row["kind"], messages=row["messages"],
                       updated=row["updated"], path=row["path"])
    return 0


def _show_session(name: str, reporter: Reporter) -> int:
    messages = load_messages(name)
    turns = [] if messages else load_session(name)
    if not messages and not turns:
        reporter.error(f"Forgefy: no saved session named '{name}'. Run forgefy history.")
        return 1
    if messages:
        for message in messages:
            content = message.get('content') or ''
            if message.get('tool_calls'):
                content = (content + '\n' if content else '') + json.dumps(message['tool_calls'], ensure_ascii=False)
            reporter.event('message', f"{message.get('role')}> {_clip(content)}",
                           role=message.get('role'), content=content)
        return 0
    for role, content in turns:
        reporter.event('message', f"{role}> {_clip(content)}", role=role, content=content)
    return 0


def _auth(args: argparse.Namespace, reporter: Reporter, providers: dict[str, Provider]) -> int:
    requested = (getattr(args, 'provider', None) or '').strip()
    if requested in {'', 'forgefy'}:
        with httpx.Client(timeout=15) as http:
            return device_login(http, api_url=_opt(args, 'api_url', None), output=reporter.info)
    if requested not in providers:
        raise ValueError(f"Unknown provider '{requested}'. Run forgefy providers.")
    provider = providers[requested]
    if not provider.api_key_env:
        reporter.info(f"{requested} needs no API key ({provider.base_url}).")
        return 0
    if os.environ.get(provider.api_key_env):
        reporter.info(f"{provider.api_key_env} is set, so {requested} is ready. Set "
                      f'default_provider = "{requested}" in {config_path()} to stop passing -P.')
        return 0
    reporter.error(f"Forgefy: {requested} reads its key from ${provider.api_key_env}, which is not set. "
                   "Export it — forgefy never stores provider keys — then set "
                   f'default_provider = "{requested}" in {config_path()}.')
    return 1


def _update(reporter: Reporter) -> int:
    current = installed_version()
    with httpx.Client(timeout=httpx.Timeout(10, connect=5)) as http:
        latest = latest_version(http)
    if latest is None:
        reporter.error("Forgefy: could not reach PyPI to check for updates.")
        return 1
    if compare(current, latest) < 0:
        reporter.info(f"Update available: {current} -> {latest}. Re-run the installer you used "
                      "(install.sh / install.ps1), or: pipx upgrade forgefy-cli")
        return 0
    reporter.info(f"Up to date: {current} is the latest release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
