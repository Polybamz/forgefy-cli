"""Forgefy command-line interface."""
from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import sys

import httpx

from .auth import bootstrap_env
from .chat import chat_loop, print_fragment
from .config import TEMPLATE, config_path, load_config
from .context import build_prompt
from .editing import edit_files
from .history import load_session, save_session, session_path
from .providers import ModelClient, ProviderError
from .skills import SKILLS, system_prompt

try:
    _VERSION = version("forgefy-cli")
except PackageNotFoundError:  # running from source without an install record
    _VERSION = "0.0.0-dev"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="forgefy", description="Forgefy: local or hosted coding assistance. Run/chat suggest; edit applies approved file changes and, with --allow-commands, runs approved shell commands.")
    result.add_argument("--version", action="version", version=f"Forgefy CLI {_VERSION}")
    sub = result.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Send one coding request and print the response")
    run.add_argument("prompt", help="Coding request; use '-' to read from stdin")
    run.add_argument("--provider", help="Provider profile name (default: config or ollama)")
    run.add_argument("--model", help="Exact provider model ID; no automatic paid fallback")
    run.add_argument("--workspace", type=Path, default=Path.cwd())
    run.add_argument("--file", action="append", default=[], help="Explicit relative file to send; repeatable. Review for secrets first.")
    run.add_argument("--skill", choices=sorted(SKILLS), default="code")
    run.add_argument("--skill-file", type=Path, action="append", default=[], help="Trusted Markdown instructions to send; repeatable")
    run.add_argument("--no-stream", action="store_true", help="Wait for the full response instead of printing it as it streams")
    chat = sub.add_parser("chat", help="Multi-turn conversation; replies are suggestions to review")
    chat.add_argument("--provider", help="Provider profile name (default: config or ollama)")
    chat.add_argument("--model", help="Exact provider model ID; no automatic paid fallback")
    chat.add_argument("--skill", choices=sorted(SKILLS), default="code")
    chat.add_argument("--skill-file", type=Path, action="append", default=[], help="Trusted Markdown instructions to send; repeatable")
    chat.add_argument("--no-stream", action="store_true", help="Wait for each full response instead of printing it as it streams")
    chat.add_argument("--session", default="default", help="Named session to save to disk (default: 'default')")
    chat.add_argument("--resume", action="store_true", help="Load previous turns from --session before starting; without this, every run starts fresh (but is still saved)")
    chat.add_argument("--no-history", action="store_true", help="Don't load or save this session at all; ephemeral like before")
    edit = sub.add_parser("edit", help="Edit explicitly selected existing files with approval for every diff")
    edit.add_argument("prompt", help="Requested change")
    edit.add_argument("--provider", help="Provider profile; requires a tool-calling model")
    edit.add_argument("--model", help="Exact provider model ID")
    edit.add_argument("--workspace", type=Path, default=Path.cwd())
    edit.add_argument("--file", action="append", default=[], help="Allowed existing relative file; repeatable")
    edit.add_argument("--create", action="append", default=[], help="Approved relative path to create; repeatable, parent dir must exist")
    edit.add_argument("--max-turns", type=int, default=12, help="Maximum model requests (1–30; default 12)")
    edit.add_argument("--allow-commands", action="store_true",
                       help="Let the model propose shell commands (e.g. to run tests); each still requires your approval. Not sandboxed.")
    edit.add_argument("--command-timeout", type=int, default=120, help="Seconds before an approved command is killed (default 120)")
    models = sub.add_parser("models", help="List live provider model IDs (availability and pricing vary)")
    models.add_argument("--provider")
    login = sub.add_parser("login", help="Sign in with your Forgefy account via the browser (device code)")
    login.add_argument("--api-url", help="Override FORGEFY_API_URL for this login only")
    sub.add_parser("logout", help="Remove the locally stored Forgefy account credential")
    sub.add_parser("providers", help="List built-in and configured provider plugins")
    sub.add_parser("skills", help="List built-in coding skills")
    sub.add_parser("doctor", help="Check configuration and key presence without sending requests")
    config = sub.add_parser("config", help="Display config path, or create a starter file")
    config.add_argument("--init", action="store_true", help="Create a starter config; never overwrite")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    bootstrap_env()
    try:
        if args.command == "login":
            from .login import device_login
            with httpx.Client(timeout=15) as http:
                return device_login(http, api_url=args.api_url)
        if args.command == "logout":
            from .login import logout
            return logout()
        if args.command == "config":
            path = config_path()
            if args.init:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(TEMPLATE)
            print(path)
            return 0
        if args.command == "skills":
            for name, description in SKILLS.items():
                print(f"{name}: {description}")
            return 0
        settings, providers = load_config()
        if args.command in {"providers", "doctor"}:
            for name, provider in providers.items():
                status = "no key required" if not provider.api_key_env else f"{provider.api_key_env}: {'set' if os.environ.get(provider.api_key_env) else 'missing'}"
                print(f"{name}\t{provider.base_url}\t{status}")
            if args.command == "doctor":
                print(f"Config: {config_path()}")
                print("Network/model availability not checked. Use models --provider NAME.")
            return 0
        name = args.provider or settings.get("default_provider", "ollama")
        if name not in providers:
            raise ValueError(f"Unknown provider '{name}'. Run forgefy providers.")
        provider = providers[name]
        coding = args.command in {"run", "chat", "edit"}
        if coding:
            # A default model belongs to its configured provider, not an override.
            model = args.model or (settings.get("default_model") if name == settings.get("default_provider", "ollama") else None)
            if not model or not model.strip():
                raise ValueError("Choose --model ID (see forgefy models), or set default_model in config.")
            if args.command != "edit":
                system = system_prompt(args.skill, args.skill_file)
        with httpx.Client(timeout=httpx.Timeout(120, connect=10)) as http:
            client = ModelClient(provider, http)
            if args.command == "edit":
                return edit_files(client, model, args.prompt, args.workspace, args.file, args.max_turns,
                                   args.create, args.allow_commands, args.command_timeout)
            if args.command == "models":
                for model_id in client.models():
                    print(model_id)
            elif args.command == "run":
                prompt = sys.stdin.read(120001) if args.prompt == "-" else args.prompt
                prompt = build_prompt(prompt, args.workspace, args.file)
                print(f"Sending request to {name} / {model}. Provider pricing applies; no fallback.", file=sys.stderr)
                if args.no_stream:
                    print(client.complete(model, system, prompt))
                else:
                    client.complete(model, system, prompt, on_token=print_fragment)
                    print()
            else:
                print(f"Chatting with {name} / {model}. /exit to leave; replies are suggestions to review, never executed.", file=sys.stderr)
                if args.no_history:
                    initial_history, on_turn = [], (lambda h: None)
                else:
                    initial_history = load_session(args.session) if args.resume else []
                    on_turn = lambda h: save_session(args.session, h)  # noqa: E731
                    if args.resume and initial_history:
                        print(f"Resumed session '{args.session}' ({len(initial_history) // 2} previous turn(s)). "
                              f"/new to start fresh.", file=sys.stderr)
                    elif args.resume:
                        print(f"No previous history for session '{args.session}' — starting fresh.", file=sys.stderr)
                    else:
                        print(f"Session '{args.session}' will be saved to {session_path(args.session)}. "
                              f"Use --resume to continue it next time.", file=sys.stderr)
                chat_on_token = None if args.no_stream else print_fragment
                chat_loop(client, model, system, on_token=chat_on_token, history=initial_history, on_turn=on_turn)
        return 0
    except (ValueError, OSError, ProviderError) as exc:
        print(f"Forgefy: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
