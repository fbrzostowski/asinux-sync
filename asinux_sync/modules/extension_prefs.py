"""Read and write one GNOME Shell extension's settings (a dconf folder)."""

from __future__ import annotations

import subprocess

from .base import ModuleUnavailable


def path(uuid: str) -> str:
    # The convention asinux-layouts uses: /org/gnome/shell/extensions/<name>/
    return f"/org/gnome/shell/extensions/{uuid.split('@')[0]}/"


def _dconf(*args: str, stdin: str | None = None) -> str:
    try:
        result = subprocess.run(["dconf", *args], input=stdin, capture_output=True, text=True, timeout=10)
    except FileNotFoundError as exc:
        raise ModuleUnavailable("the dconf command is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ModuleUnavailable("dconf did not answer") from exc
    if result.returncode != 0:
        raise ModuleUnavailable(result.stderr.strip() or f"dconf {args[0]} failed")
    return result.stdout


def dump(uuid: str) -> str:
    return _dconf("dump", path(uuid))


def apply(uuid: str, wanted: str) -> None:
    """Make the extension's settings exactly `wanted`; touches nothing if they already are."""
    folder = path(uuid)
    if not wanted.strip() or _dconf("dump", folder) == wanted:
        return
    _dconf("reset", "-f", folder)
    _dconf("load", folder, stdin=wanted)
