from __future__ import annotations

from pathlib import Path

from . import gsettings_util
from .gsettings_module import GSettingsModule

# Same schema list and key filter as shortcuts_settings.py's
# discover_system_shortcuts() - rediscovered here rather than imported so
# this package doesn't need the settings app (and GTK) on its path.
SHORTCUT_SCHEMAS = (
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.mutter.keybindings",
    "org.gnome.mutter.wayland.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
)
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = f"{MEDIA_KEYS}.custom-keybinding"
CUSTOM_KEYS = ("name", "command", "binding")


class ShortcutsModule(GSettingsModule):
    module_id = "shortcuts"
    display_name = "Keyboard shortcuts"
    description = "System shortcuts and your custom shortcuts"
    category = "Input"

    def targets(self) -> dict[str, tuple[str, ...]]:
        result = {}
        for schema_id in SHORTCUT_SCHEMAS:
            found = gsettings_util.schema(schema_id)
            if found is None:
                continue
            result[schema_id] = tuple(
                key
                for key in found.list_keys()
                if key != "custom-keybindings"
                and not key.endswith("-static")
                and found.get_key(key).get_value_type().dup_string() == "as"
            )
        return result

    def capture(self, assets_dir: Path) -> dict:
        data = super().capture(assets_dir)
        custom = []
        for path in gsettings_util.get_strv(MEDIA_KEYS, "custom-keybindings"):
            values = gsettings_util.capture_keys(CUSTOM_SCHEMA, CUSTOM_KEYS, path) or {}
            custom.append({"path": path, "values": values})
        data["custom"] = custom
        return data

    def apply(self, data: dict, assets_dir: Path) -> None:
        super().apply(data, assets_dir)
        if "custom" not in data:
            return
        wanted = data["custom"]
        wanted_paths = [entry["path"] for entry in wanted]
        for entry in wanted:
            gsettings_util.apply_keys(CUSTOM_SCHEMA, CUSTOM_KEYS, entry["values"], entry["path"])
        # Entries that exist only here are removed, so the cloud's list wins.
        for stale in gsettings_util.get_strv(MEDIA_KEYS, "custom-keybindings"):
            if stale not in wanted_paths:
                gsettings_util.apply_keys(CUSTOM_SCHEMA, CUSTOM_KEYS, {}, stale)
        gsettings_util.set_strv(MEDIA_KEYS, "custom-keybindings", wanted_paths)
