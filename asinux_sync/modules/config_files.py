"""Whole configuration files that apps or extensions keep outside GSettings.

Each file is one item. Adding another is one line in FILES. A file is synced
as text; one that doesn't exist on a computer is "nothing set here" - it is
neither uploaded as empty nor deleted on the others.
"""

from __future__ import annotations

from pathlib import Path

from .base import Item, SyncModule

MAX_BYTES = 1024 * 1024


def _config_dir() -> Path:
    # GLib caches the config dir at startup; follow HOME like the rest of the app.
    return Path.home() / ".config"


# item id -> (name, description, path relative to ~/.config)
FILES = {
    "custom_menu": ("Custom Menu", "Entries of the Custom Menu extension (custom-menu.json)", "custom-menu.json"),
}


def file_path(item_id: str) -> Path:
    return _config_dir() / FILES[item_id][2]


class ConfigFilesModule(SyncModule):
    module_id = "config_files"
    display_name = "Configuration files"
    description = "Settings that apps and extensions keep in their own files"
    category = "Look and feel"

    def list_items(self) -> list[Item]:
        return [Item(item_id, name, description) for item_id, (name, description, _p) in FILES.items()]

    def is_default(self, item_id: str, data: dict) -> bool:
        return data.get("text") is None

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        path = file_path(item_id)
        try:
            if path.stat().st_size > MAX_BYTES:
                return {"text": None}
            return {"text": path.read_text(encoding="utf-8")}
        except (OSError, UnicodeDecodeError):
            return {"text": None}

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        text = data.get("text")
        if text is None:
            return  # not set on the other computer: keep this one's file, if any
        path = file_path(item_id)
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except (OSError, UnicodeDecodeError):
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".asinux-sync.tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
