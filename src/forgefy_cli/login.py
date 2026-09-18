"""Browser device-code login — `forgefy login`.

Mirrors GitHub/gh CLI's device flow: get a code from the API, open the
confirmation page in a browser, poll until the person approves it there.
See forgefy-backend's app/api/v1/device_auth.py for the server half.
"""
from __future__ import annotations

import os
import sys
import time
import webbrowser
from typing import Callable

import httpx

from .auth import clear_credentials, credentials_path, save_credentials
from .config import Provider, config_path, forgefy_api_url, load_config, set_defaults
from .providers import ModelClient, ProviderError

# Generous ceiling on top of whatever the server reports, in case a
# malformed/missing expires_in would otherwise poll forever.
_MAX_WAIT_SECONDS = 900


def _offer_default_model(
    http: httpx.Client, base: str, api_key: str,
    output: Callable[[str], None], input_fn: Callable[[str], str], interactive: bool,
) -> None:
    """Cline-style "configure once": after login, offer to pick and save a
    default model so a bare `forgefy chat`/`forgefy run "..."` works with no
    --provider/--model from now on (config.py's set_defaults). Best-effort —
    login has already succeeded by the time this runs, so any failure here
    just falls back to typing --model each time, never fails the command.
    """
    if not interactive:
        output("Non-interactive session — pass --provider forgefy --model <id> each time, "
                f"or set default_provider/default_model in {config_path()}.")
        return
    try:
        settings, _ = load_config()
    except ValueError:
        settings = {}
    if settings.get("default_provider") or settings.get("default_model"):
        output(f"Defaults already set in {config_path()} — leaving them as-is.")
        return  # don't clobber an existing choice on re-login

    os.environ["FORGEFY_API_KEY"] = api_key
    provider = Provider("forgefy", f"{base}/api/v1/cli", "FORGEFY_API_KEY")
    try:
        model_ids = ModelClient(provider, http).models()
    except (ProviderError, ValueError) as exc:
        output(f"Could not list models to set a default ({exc}) — use --model each time.")
        return
    if not model_ids:
        return

    output("\nAvailable models on your plan:")
    for i, model_id in enumerate(model_ids, 1):
        output(f"  {i}. {model_id}")
    try:
        choice = input_fn(f"Pick a default model [1-{len(model_ids)}], or Enter to skip: ").strip()
    except EOFError:
        return
    if not choice:
        output(f"Skipped — pass --model each time, or set default_model in {config_path()} later.")
        return
    try:
        index = int(choice)
        if not 1 <= index <= len(model_ids):
            raise ValueError
    except ValueError:
        output("Not a valid choice — skipped.")
        return

    saved = set_defaults("forgefy", model_ids[index - 1])
    output(f"Saved as your default in {saved}. Just run 'forgefy chat' or "
           "'forgefy run \"...\"' from now on.")


def device_login(
    http: httpx.Client,
    *,
    api_url: str | None = None,
    open_browser: Callable[[str], bool] = webbrowser.open,
    output: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> int:
    base = (api_url or forgefy_api_url()).rstrip("/")
    interactive = sys.stdin.isatty() if interactive is None else interactive

    try:
        resp = http.post(f"{base}/api/v1/auth/device/start", json={})
        resp.raise_for_status()
        start = resp.json()
    except httpx.HTTPStatusError as exc:
        output(f"Forgefy: could not start login (HTTP {exc.response.status_code}).")
        return 1
    except httpx.RequestError as exc:
        output(f"Forgefy: cannot reach {base}: {exc}")
        return 1

    try:
        device_code = start["device_code"]
        user_code = start["user_code"]
        verify_url = start["verification_uri_complete"]
        interval = max(1, int(start.get("interval", 5)))
        expires_in = min(int(start.get("expires_in", 600)), _MAX_WAIT_SECONDS)
    except (KeyError, TypeError, ValueError):
        output("Forgefy: unexpected response starting login.")
        return 1

    output(f"Confirmation code: {user_code}")
    output(f"Opening {verify_url} in your browser...")
    output("If it doesn't open, or you're asked to log in first, visit that URL yourself")
    output(f"and enter the code above (it stays valid for {expires_in // 60} minutes).")
    try:
        open_browser(verify_url)
    except Exception:
        pass  # headless/SSH session — the printed URL above is the fallback

    deadline = now() + expires_in
    while now() < deadline:
        sleep(interval)
        try:
            poll = http.post(f"{base}/api/v1/auth/device/poll", json={"device_code": device_code})
            poll.raise_for_status()
            body = poll.json()
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            continue  # transient hiccup — keep polling until the deadline

        status = body.get("status")
        if status == "approved":
            api_key = body.get("api_key")
            if not isinstance(api_key, str) or not api_key:
                output("Forgefy: login approved but no key was returned — try again.")
                return 1
            path = save_credentials(api_key)
            output(f"Logged in. Credential saved to {path}.")
            _offer_default_model(http, base, api_key, output, input_fn, interactive)
            return 0
        if status == "denied":
            output("Forgefy: login was denied.")
            return 1
        if status == "expired":
            output("Forgefy: login code expired — run 'forgefy login' again.")
            return 1
        # "pending" — keep polling.

    output("Forgefy: login timed out — run 'forgefy login' again.")
    return 1


def logout() -> int:
    if clear_credentials():
        print(f"Removed {credentials_path()}. You're logged out.")
    else:
        print("Not logged in (no stored credential).")
    return 0
