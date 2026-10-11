from __future__ import annotations

from pathlib import Path

from . import gsettings_util
from .base import Item
from .gsettings_module import Group, GSettingsModule

# Same schema list and key filter as shortcuts_settings.py's
# discover_system_shortcuts() - rediscovered here rather than imported so
# this package doesn't need the settings app (and GTK) on its path.
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = f"{MEDIA_KEYS}.custom-keybinding"
CUSTOM_KEYS = ("name", "command", "binding")
CUSTOM = "custom"

SYSTEM_GROUPS = (
    ("windows", "Window shortcuts", "Moving, tiling, switching and closing windows and workspaces",
     ("org.gnome.desktop.wm.keybindings",)),
    ("shell", "Shell shortcuts", "Overview, app grid, notifications, screenshots",
     ("org.gnome.shell.keybindings",)),
    ("mutter", "Compositor shortcuts", "Mutter and Wayland-specific shortcuts",
     ("org.gnome.mutter.keybindings", "org.gnome.mutter.wayland.keybindings")),
    ("media_keys", "Media and system keys", "Volume, media, launchers, lock screen and similar keys",
     (MEDIA_KEYS,)),
)


def _shortcut_keys(schema_id: str) -> tuple[str, ...]:
    found = gsettings_util.schema(schema_id)
    if found is None:
        return ()
    return tuple(
        key
        for key in found.list_keys()
        if key != "custom-keybindings"
        and not key.endswith("-static")
        and found.get_key(key).get_value_type().dup_string() == "as"
    )


class ShortcutsModule(GSettingsModule):
    module_id = "shortcuts"
    display_name = "Keyboard shortcuts"
    description = "System shortcuts and your own"
    category = "Input"

    def group_specs(self):
        return tuple(
            Group(item_id, name, description, {schema_id: _shortcut_keys(schema_id) for schema_id in schemas})
            for item_id, name, description, schemas in SYSTEM_GROUPS
        )

    def list_items(self) -> list[Item]:
        return super().list_items() + [Item(CUSTOM, "Custom shortcuts", "Shortcuts you added yourself")]

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        if item_id != CUSTOM:
            return super().capture_item(item_id, assets_dir)
        entries = []
        for path in gsettings_util.get_strv(MEDIA_KEYS, "custom-keybindings"):
            values = gsettings_util.capture_keys(CUSTOM_SCHEMA, CUSTOM_KEYS, path) or {}
            entries.append({"path": path, "values": values})
        return {"custom": entries}

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        if item_id != CUSTOM:
            return super().apply_item(item_id, data, assets_dir)
        wanted = data.get("custom", [])
        wanted_paths = [entry["path"] for entry in wanted]
        for entry in wanted:
            gsettings_util.apply_keys(CUSTOM_SCHEMA, CUSTOM_KEYS, entry["values"], entry["path"])
        # Entries that exist only here are removed, so the cloud's list wins.
        for stale in gsettings_util.get_strv(MEDIA_KEYS, "custom-keybindings"):
            if stale not in wanted_paths:
                gsettings_util.apply_keys(CUSTOM_SCHEMA, CUSTOM_KEYS, {}, stale)
        gsettings_util.set_strv(MEDIA_KEYS, "custom-keybindings", wanted_paths)
