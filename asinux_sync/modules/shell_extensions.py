"""What GNOME Shell extensions are installed here, and which are enabled."""

from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from . import gsettings_util

SHELL = "org.gnome.shell"
ENABLED_KEY = "enabled-extensions"


def installed() -> dict[str, str]:
    """uuid -> display name, user extensions taking precedence over system ones."""
    roots = [Path(GLib.get_user_data_dir())] + [Path(d) for d in GLib.get_system_data_dirs()]
    found: dict[str, str] = {}
    for root in roots:
        directory = root / "gnome-shell" / "extensions"
        if not directory.is_dir():
            continue
        for folder in sorted(directory.iterdir()):
            try:
                metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # not an extension (stray files, half-installed ones)
            found.setdefault(metadata.get("uuid") or folder.name, metadata.get("name") or folder.name)
    return found


def is_enabled(uuid: str) -> bool:
    return uuid in gsettings_util.get_strv(SHELL, ENABLED_KEY)


def set_enabled(uuid: str, enabled: bool) -> None:
    current = gsettings_util.get_strv(SHELL, ENABLED_KEY)
    if enabled and uuid not in current:
        gsettings_util.set_strv(SHELL, ENABLED_KEY, current + [uuid])
    elif not enabled and uuid in current:
        gsettings_util.set_strv(SHELL, ENABLED_KEY, [u for u in current if u != uuid])
