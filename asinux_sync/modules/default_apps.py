from __future__ import annotations

import os
from pathlib import Path

from .base import SyncModule


def mimeapps_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "mimeapps.list"


class DefaultAppsModule(SyncModule):
    module_id = "default_apps"
    display_name = "Default apps"
    description = "Which app opens each file type and link"
    category = "System"

    # default_apps_settings.py edits this same file (through Gio.AppInfo), so
    # syncing the plain XDG file is equivalent and needs no GIO round-trips.
    def capture(self, assets_dir: Path) -> dict:
        try:
            return {"mimeapps": mimeapps_path().read_text(encoding="utf-8")}
        except OSError:
            return {"mimeapps": None}

    def apply(self, data: dict, assets_dir: Path) -> None:
        text = data.get("mimeapps")
        if text is None:
            return
        path = mimeapps_path()
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
