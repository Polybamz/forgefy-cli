"""Is a newer release available? — read-only PyPI version check.

Used to nudge the person running the CLI when a newer `forgefy-cli` has been
published. This module only *asks* PyPI what the latest version is: it never
downloads, installs, or replaces anything, and every failure degrades quietly
to "no answer" so it can never break a command.
"""
from __future__ import annotations

import importlib.metadata

import httpx

_PACKAGE = "forgefy-cli"
_PYPI_URL = f"https://pypi.org/pypi/{_PACKAGE}/json"
_FALLBACK_VERSION = "0.0.0-dev"  # running from source without an install record
_DIGITS = "0123456789"


def installed_version() -> str:
    """The installed package version, or the source-checkout fallback."""
    try:
        return importlib.metadata.version(_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return _FALLBACK_VERSION


def latest_version(http: httpx.Client) -> str | None:
    """Latest published version from PyPI, or None on any failure.

    The caller owns the client (and its timeout); this only issues one GET and
    interprets the JSON, returning None rather than raising on errors such as
    a network failure, a non-2xx status, invalid JSON, or an unexpected shape.
    """
    try:
        response = http.get(_PYPI_URL)
        response.raise_for_status()
        data = response.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
        return None
    info = data.get("info") if isinstance(data, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) else None


def _segment(value: str) -> int:
    """Lead numeric prefix of one version segment ("3rc1" -> 3, "rc1" -> 0)."""
    digits = ""
    for char in value:
        if char not in _DIGITS:
            break
        digits += char
    return int(digits) if digits else 0


def compare(left: str, right: str) -> int:
    """Compare two dotted versions without `packaging`: -1, 0 or 1.

    Segments are compared left to right; a missing segment counts as 0 and any
    non-numeric suffix is ignored, so "0.3.1rc1" == "0.3.1" and "1.2" == "1.2.0".
    """
    left_parts = left.split(".")
    right_parts = right.split(".")
    for index in range(max(len(left_parts), len(right_parts))):
        left_value = _segment(left_parts[index]) if index < len(left_parts) else 0
        right_value = _segment(right_parts[index]) if index < len(right_parts) else 0
        if left_value != right_value:
            return -1 if left_value < right_value else 1
    return 0
