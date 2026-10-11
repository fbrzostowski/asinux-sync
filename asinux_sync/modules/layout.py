from __future__ import annotations

from pathlib import Path

from . import extension_prefs, shell_extensions
from .base import SyncModule


class LayoutModule(SyncModule):
    module_id = "layout"
    display_name = "Layout"
    description = "How your extensions are configured: dock, panel, menus, effects"
    category = "Look and feel"
    default_enabled = False

    # One switch, settings only - never on/off (that is the Extensions
    # module) - and independent of asinux-layouts: the same dconf folders it
    # uses, read and written directly.
    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        settings = {}
        for uuid in sorted(shell_extensions.installed()):
            dump = extension_prefs.dump(uuid)
            if dump.strip():
                settings[uuid] = dump
        return {"settings": settings} if settings else None

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        installed = shell_extensions.installed()
        for uuid, wanted in data.get("settings", {}).items():
            if uuid in installed:
                extension_prefs.apply(uuid, wanted)

    def merge_item(self, item_id: str, remote: dict, local: dict) -> dict:
        # Keep the settings of extensions this machine doesn't have.
        return {"settings": {**remote.get("settings", {}), **local["settings"]}}
