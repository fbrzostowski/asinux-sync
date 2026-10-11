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


def _extension_dirs() -> list[Path]:
    """Folders that hold extensions, user ones before system ones."""
    roots = [Path(GLib.get_user_data_dir())] + [Path(d) for d in GLib.get_system_data_dirs()]
    return [root / "gnome-shell" / "extensions" for root in roots]


def _scan() -> dict[str, tuple[str, Path]]:
    found: dict[str, tuple[str, Path]] = {}
    for directory in _extension_dirs():
        if not directory.is_dir():
            continue
        for folder in sorted(directory.iterdir()):
            try:
                metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # not an extension (stray files, half-installed ones)
            found.setdefault(metadata.get("uuid") or folder.name, (metadata.get("name") or folder.name, folder))
    return found


def installed() -> dict[str, str]:
    """uuid -> display name, user extensions taking precedence over system ones."""
    return {uuid: name for uuid, (name, _folder) in _scan().items()}


def extension_dir(uuid: str) -> Path | None:
    entry = _scan().get(uuid)
    return entry[1] if entry else None


def is_enabled(uuid: str) -> bool:
    return uuid in gsettings_util.get_strv(SHELL, ENABLED_KEY)


def set_enabled(uuid: str, enabled: bool) -> None:
    current = gsettings_util.get_strv(SHELL, ENABLED_KEY)
    if enabled and uuid not in current:
        gsettings_util.set_strv(SHELL, ENABLED_KEY, current + [uuid])
    elif not enabled and uuid in current:
        gsettings_util.set_strv(SHELL, ENABLED_KEY, [u for u in current if u != uuid])
