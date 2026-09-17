"""Provider profiles. Credentials are read only from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key_env: str = ""

    def headers(self) -> dict[str, str]:
        if not self.api_key_env:
            return {}
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ValueError(f"Set {self.api_key_env} before using {self.name}.")
        return {"Authorization": f"Bearer {key}"}


BUILTINS = {
    "ollama": Provider("ollama", "http://localhost:11434/v1"),
    "openai": Provider("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": Provider("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": Provider("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "groq": Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
}

TEMPLATE = '''# Keep API keys in environment variables, never here.
default_provider = "ollama"
# default_model = "your-installed-model"

# Provider plugin using the OpenAI chat-completions protocol:
# [providers.my_server]
# base_url = "http://localhost:1234/v1"
# api_key_env = ""
'''


def config_path() -> Path:
    return Path(os.environ.get("FORGEFY_CONFIG", str(Path.home() / ".forgefy" / "config.toml"))).expanduser()


def validate_provider(name: str, value: dict) -> Provider:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        raise ValueError("Provider names must contain letters, digits, underscores or hyphens.")
    url = value.get("base_url", "")
    env = value.get("api_key_env", "")
    if not isinstance(url, str) or not isinstance(env, str):
        raise ValueError(f"Invalid provider profile: {name}")
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"Invalid base URL for {name}; do not embed credentials.")
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("Provider URLs require HTTPS, except for localhost servers.")
    if env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env):
        raise ValueError(f"Invalid API key environment variable name for {name}.")
    return Provider(name, url.rstrip("/"), env)


def load_config(path: Path | None = None) -> tuple[dict, dict[str, Provider]]:
    path = path or config_path()
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    providers = dict(BUILTINS)
    profiles = data.get("providers", {})
    if not isinstance(profiles, dict):
        raise ValueError("providers must be a TOML table.")
    for name, value in profiles.items():
        if not isinstance(value, dict):
            raise ValueError(f"Invalid provider profile: {name}")
        if name in BUILTINS:
            raise ValueError(f"Use a new profile name instead of overriding built-in {name}.")
        providers[name] = validate_provider(name, value)
    for field in ("default_provider", "default_model"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            raise ValueError(f"{field} must be a nonempty string.")
    return data, providers

