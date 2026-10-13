from __future__ import annotations

from pathlib import Path

from . import gsettings_util
from .base import Item
from .gsettings_module import Group, GSettingsModule

FOLDERS = "org.gnome.desktop.app-folders"
FOLDER = f"{FOLDERS}.folder"
FOLDER_PATH = "/org/gnome/desktop/app-folders/folders/"
FOLDER_KEYS = ("name", "translate", "apps", "excluded-apps", "categories")
FOLDERS_ITEM = "folders"


class AppGridModule(GSettingsModule):
    module_id = "app_grid"
    display_name = "App grid"
    description = "The order of apps on the app grid pages and your app folders"
    category = "Look and feel"

    groups = (
        Group("order", "App order", "Which page and position each app has", {"org.gnome.shell": ("app-picker-layout",)}),
    )

    def list_items(self) -> list[Item]:
        return super().list_items() + [Item(FOLDERS_ITEM, "App folders", "The folders on the app grid and what is in them")]

    def is_default(self, item_id: str, data: dict) -> bool:
        if item_id == FOLDERS_ITEM:
            return not data.get("children") and not any(data.get("folders", {}).values())
        return super().is_default(item_id, data)

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        if item_id != FOLDERS_ITEM:
            return super().capture_item(item_id, assets_dir)
        return {
            "children": gsettings_util.capture_keys(FOLDERS, ("folder-children",)) or {},
            "folders": {
                folder: gsettings_util.capture_keys(FOLDER, FOLDER_KEYS, FOLDER_PATH + folder + "/") or {}
                for folder in gsettings_util.get_strv(FOLDERS, "folder-children")
            },
        }

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        if item_id != FOLDERS_ITEM:
            return super().apply_item(item_id, data, assets_dir)
        before = gsettings_util.get_strv(FOLDERS, "folder-children")
        gsettings_util.apply_keys(FOLDERS, ("folder-children",), data.get("children", {}))
        for folder, values in data.get("folders", {}).items():
            gsettings_util.apply_keys(FOLDER, FOLDER_KEYS, values, FOLDER_PATH + folder + "/")
        # Folders that were dropped from the list: forget what they contained.
        remaining = set(gsettings_util.get_strv(FOLDERS, "folder-children"))
        for folder in before:
            if folder not in remaining:
                gsettings_util.apply_keys(FOLDER, FOLDER_KEYS, {}, FOLDER_PATH + folder + "/")
