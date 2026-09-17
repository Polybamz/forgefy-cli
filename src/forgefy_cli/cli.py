"""Forgefy command-line interface."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import httpx

from .auth import bootstrap_env
from .chat import chat_loop
from .config import TEMPLATE, config_path, load_config
from .context import build_prompt
from .editing import edit_files
from .providers import ModelClient, ProviderError
from .skills import SKILLS, system_prompt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="forgefy", description="Forgefy: local or hosted coding assistance. Run/chat suggest; edit applies approved file changes. No command execution.")
    result.add_argument("--version", action="version", version="Forgefy CLI 0.1.0")
    sub = result.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Send one coding request and print the response")
    run.add_argument("prompt", help="Coding request; use '-' to read from stdin")
    run.add_argument("--provider", help="Provider profile name (default: config or ollama)")
    run.add_argument("--model", help="Exact provider model ID; no automatic paid fallback")
    run.add_argument("--workspace", type=Path, default=Path.cwd())
    run.add_argument("--file", action="append", default=[], help="Explicit relative file to send; repeatable. Review for secrets first.")
    run.add_argument("--skill", choices=sorted(SKILLS), default="code")
    run.add_argument("--skill-file", type=Path, action="append", default=[], help="Trusted Markdown instructions to send; repeatable")
    chat = sub.add_parser("chat", help="Multi-turn conversation; replies are suggestions to review")
    chat.add_argument("--provider", help="Provider profile name (default: config or ollama)")
    chat.add_argument("--model", help="Exact provider model ID; no automatic paid fallback")
    chat.add_argument("--skill", choices=sorted(SKILLS), default="code")
    chat.add_argument("--skill-file", type=Path, action="append", default=[], help="Trusted Markdown instructions to send; repeatable")
    edit = sub.add_parser("edit", help="Edit explicitly selected existing files with approval for every diff")
    edit.add_argument("prompt", help="Requested change")
    edit.add_argument("--provider", help="Provider profile; requires a tool-calling model")
    edit.add_argument("--model", help="Exact provider model ID")
    edit.add_argument("--workspace", type=Path, default=Path.cwd())
    edit.add_argument("--file", action="append", default=[], help="Allowed existing relative file; repeatable")
    edit.add_argument("--create", action="append", default=[], help="Approved relative path to create; repeatable, parent dir must exist")
    edit.add_argument("--max-turns", type=int, default=12, help="Maximum model requests (1–30; default 12)")
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
                                return edit_files(client, model, args.prompt, args.workspace, args.file, args.max_turns, args.create)
            if args.command == "models":
                for model_id in client.models():
                    print(model_id)
            elif args.command == "run":
                prompt = sys.stdin.read(120001) if args.prompt == "-" else args.prompt
                prompt = build_prompt(prompt, args.workspace, args.file)
                print(f"Sending request to {name} / {model}. Provider pricing applies; no fallback.", file=sys.stderr)
                print(client.complete(model, system, prompt))
            else:
                print(f"Chatting with {name} / {model}. /exit to leave; replies are suggestions to review, never executed.", file=sys.stderr)
                chat_loop(client, model, system)
        return 0
    except (ValueError, OSError, ProviderError) as exc:
        print(f"Forgefy: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
