"""Browser device-code login — `forgefy login`.

Mirrors GitHub/gh CLI's device flow: get a code from the API, open the
confirmation page in a browser, poll until the person approves it there.
See forgefy-backend's app/api/v1/device_auth.py for the server half.
"""
from __future__ import annotations

import time
import webbrowser
from typing import Callable

import httpx

from .auth import clear_credentials, credentials_path, save_credentials
from .config import forgefy_api_url

# Generous ceiling on top of whatever the server reports, in case a
# malformed/missing expires_in would otherwise poll forever.
_MAX_WAIT_SECONDS = 900


def device_login(
    http: httpx.Client,
    *,
    api_url: str | None = None,
    open_browser: Callable[[str], bool] = webbrowser.open,
    output: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> int:
    base = (api_url or forgefy_api_url()).rstrip("/")

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
            output("Use --provider forgefy (or set it as default_provider) from now on.")
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
