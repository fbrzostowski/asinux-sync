from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

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

# GTK themes that are compiled into GTK and need no folder.
BUILTIN = {"gtk_theme": {"Adwaita", "Adwaita-dark", "HighContrast", "HighContrastInverse", "Default", "Emacs"}}

# Left in a theme folder that was fetched from another device (see
# theme_requests.py): it is then not uploaded again as a user theme, since the
# device it came from has it already.
TRANSFER_MARKER = ".asinux-sync-transfer"


def _find(name: str, dirs: tuple[str, ...]) -> Path | None:
    for directory in dirs:
        candidate = Path(directory).expanduser() / name
        if candidate.is_dir():
            return candidate
    return None


def wanted_name(item_id: str, data: dict | None) -> str:
    """The theme name a cloud item asks for ("" for the default)."""
    if item_id not in THEME_ITEMS or not data:
        return ""
    schema_id, key, _dirs = THEME_ITEMS[item_id]
    text = data.get("values", {}).get(schema_id, {}).get(key)
    if not text:
        return ""
    try:
        return GLib.Variant.parse(GLib.VariantType.new("s"), text, None, None).get_string()
    except GLib.Error:
        return ""


def locate(item_id: str, name: str) -> Path | None:
    """The folder of theme `name` on this computer, if any."""
    user_dirs, system_dirs = THEME_ITEMS[item_id][2]
    return _find(name, user_dirs + system_dirs) if name else None


def is_available(item_id: str, name: str) -> bool:
    return not name or name in BUILTIN.get(item_id, ()) or locate(item_id, name) is not None


def install_dir(item_id: str) -> Path:
    """Where a theme fetched for this item is put (in the user's home)."""
    return Path(THEME_ITEMS[item_id][2][0][0]).expanduser()


def inherits(folder: Path) -> list[str]:
    """The themes an icon or cursor theme builds on (index.theme Inherits=)."""
    try:
        text = (folder / "index.theme").read_text(errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "Inherits":
            return [name.strip() for name in value.split(",") if name.strip()]
    return []


def missing(item_id: str, name: str) -> list[str]:
    """Themes this computer lacks to show `name`: the theme itself or, once
    it is here, the icon or cursor themes it inherits from."""
    result: list[str] = []
    seen: set[str] = set()
    queue = [name] if name else []
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        folder = locate(item_id, current)
        if folder is None:
            if current not in BUILTIN.get(item_id, ()):
                result.append(current)
        elif item_id in ("icon_theme", "cursor_theme"):
            queue.extend(inherits(folder))
    return result


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

    def is_default(self, item_id: str, data: dict) -> bool:
        return super().is_default(item_id, data) and "theme" not in data

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        data = super().capture_item(item_id, assets_dir)
        if item_id in THEME_ITEMS:
            schema_id, key, (user_dirs, system_dirs) = THEME_ITEMS[item_id]
            name = gsettings_util.get_string(schema_id, key)
            if name and not _find(name, system_dirs):
                source = _find(name, user_dirs)
                if source is not None and (source / TRANSFER_MARKER).exists():
                    source = None  # fetched from a device that has it: not ours to share
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
        name = wanted_name(item_id, data)
        if not is_available(item_id, name):
            # Keep this computer's own theme rather than point at a missing
            # one; theme_requests.py asks another device for it, and the item
            # is applied again once it has arrived.
            logger.info("appearance: %s %r is not installed here, not switching to it yet", item_id, name)
            return
        super().apply_item(item_id, data, assets_dir)
