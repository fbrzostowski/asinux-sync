from __future__ import annotations

import logging
from pathlib import Path

from . import assets, gsettings_util
from .gsettings_module import Group, GSettingsModule

logger = logging.getLogger(__name__)

INTERFACE = "org.gnome.desktop.interface"
USER_THEME = "org.gnome.shell.extensions.user-theme"

ICON_DIRS = (("~/.local/share/icons", "~/.icons"), ("/usr/share/icons", "/usr/local/share/icons"))
THEME_DIRS = (("~/.local/share/themes", "~/.themes"), ("/usr/share/themes", "/usr/local/share/themes"))

# item id -> (schema, key, (user dirs - the first is where themes are restored, system dirs)).
# Only items that point at an installable theme folder appear here.
THEME_ITEMS = {
    "gtk_theme": (INTERFACE, "gtk-theme", THEME_DIRS),
    "icon_theme": (INTERFACE, "icon-theme", ICON_DIRS),
    "cursor_theme": (INTERFACE, "cursor-theme", ICON_DIRS),
    "shell_theme": (USER_THEME, "name", THEME_DIRS),
}


def _find(name: str, dirs: tuple[str, ...]) -> Path | None:
    for directory in dirs:
        candidate = Path(directory).expanduser() / name
        if candidate.is_dir():
            return candidate
    return None


class AppearanceModule(GSettingsModule):
    module_id = "appearance"
    display_name = "Theme, icons and cursor"
    description = "Each part can be switched off separately. Themes you installed yourself are uploaded too"
    category = "Look and feel"

    groups = (
        Group("gtk_theme", "App theme", "GTK theme for applications", {INTERFACE: ("gtk-theme",)}),
        Group("icon_theme", "Icons", "Icon theme", {INTERFACE: ("icon-theme",)}),
        Group("cursor_theme", "Cursor theme", "Mouse pointer theme", {INTERFACE: ("cursor-theme",)}),
        Group("cursor_size", "Cursor size", "Mouse pointer size", {INTERFACE: ("cursor-size",)}),
        Group("color_scheme", "Light or dark", "Preferred color scheme", {INTERFACE: ("color-scheme",)}),
        Group("accent_color", "Accent color", "System accent color", {INTERFACE: ("accent-color",)}),
        Group("shell_theme", "Shell theme", "GNOME Shell theme (User Themes extension)", {USER_THEME: ("name",)}),
    )

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        data = super().capture_item(item_id, assets_dir)
        if item_id in THEME_ITEMS:
            schema_id, key, (user_dirs, system_dirs) = THEME_ITEMS[item_id]
            name = gsettings_util.get_string(schema_id, key)
            if name and not _find(name, system_dirs):
                source = _find(name, user_dirs)
                asset = assets.store_bundle(
                    self.module_id, f"{item_id}-{name}", assets.tree_members(source), assets_dir
                ) if source else None
                if asset:
                    data["theme"] = {"name": name, "asset": asset}
        return data

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        info = data.get("theme")
        if info and item_id in THEME_ITEMS:
            # Install the theme first, so the key never points at a missing one.
            user_dirs, system_dirs = THEME_ITEMS[item_id][2]
            if not _find(info["name"], user_dirs + system_dirs):
                if not assets.extract_bundle(info["asset"], assets_dir, Path(user_dirs[0]).expanduser()):
                    logger.warning("appearance: asset %s missing, can't install %s", info["asset"], info["name"])
        super().apply_item(item_id, data, assets_dir)
