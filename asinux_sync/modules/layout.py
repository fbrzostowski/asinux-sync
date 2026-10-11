from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from . import shell_extensions
from .base import ModuleUnavailable, SyncModule

logger = logging.getLogger(__name__)


def _path(uuid: str) -> str:
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


class LayoutModule(SyncModule):
    module_id = "layout"
    display_name = "Layout"
    description = "How your extensions are configured: dock, panel, menus, effects"
    category = "Look and feel"

    # One switch, settings only - never on/off (that is the Extensions
    # module) - and independent of asinux-layouts: the same dconf folders it
    # uses, read and written directly.
    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        settings = {}
        for uuid in sorted(shell_extensions.installed()):
            dump = _dconf("dump", _path(uuid))
            if dump.strip():
                settings[uuid] = dump
        return {"settings": settings} if settings else None

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        installed = shell_extensions.installed()
        for uuid, wanted in data.get("settings", {}).items():
            path = _path(uuid)
            if uuid not in installed or not wanted.strip() or _dconf("dump", path) == wanted:
                continue
            _dconf("reset", "-f", path)
            _dconf("load", path, stdin=wanted)

    def merge_item(self, item_id: str, remote: dict, local: dict) -> dict:
        # Keep the settings of extensions this machine doesn't have.
        return {"settings": {**remote.get("settings", {}), **local["settings"]}}
