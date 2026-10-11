from __future__ import annotations

import logging
from pathlib import Path

from . import assets, gsettings_util
from .gsettings_module import GSettingsModule

logger = logging.getLogger(__name__)

INTERFACE = "org.gnome.desktop.interface"
USER_THEME = "org.gnome.shell.extensions.user-theme"

HOME = Path.home


def _dirs(*parts: str) -> tuple[Path, ...]:
    return tuple(Path(p).expanduser() for p in parts)


# kind -> (schema, key, user dirs - first is where themes are restored, system dirs)
THEME_KINDS = {
    "icons": (INTERFACE, "icon-theme", ("~/.local/share/icons", "~/.icons"), ("/usr/share/icons", "/usr/local/share/icons")),
    "cursor": (INTERFACE, "cursor-theme", ("~/.local/share/icons", "~/.icons"), ("/usr/share/icons", "/usr/local/share/icons")),
    "gtk": (INTERFACE, "gtk-theme", ("~/.local/share/themes", "~/.themes"), ("/usr/share/themes", "/usr/local/share/themes")),
    "shell": (USER_THEME, "name", ("~/.local/share/themes", "~/.themes"), ("/usr/share/themes", "/usr/local/share/themes")),
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
    description = "GTK, shell, icon and cursor themes, color scheme and accent. "\
                  "Themes you installed yourself are uploaded too"
    category = "Look and feel"

    schemas = {
        INTERFACE: ("gtk-theme", "icon-theme", "cursor-theme", "cursor-size", "color-scheme", "accent-color"),
        USER_THEME: ("name",),
    }

    def capture(self, assets_dir: Path) -> dict:
        data = super().capture(assets_dir)
        themes = {}
        for kind, (schema_id, key, user_dirs, system_dirs) in THEME_KINDS.items():
            name = gsettings_util.get_string(schema_id, key)
            if not name or _find(name, system_dirs):
                continue  # default or shipped by the distribution
            source = _find(name, user_dirs)
            if source is None:
                continue
            asset = assets.store_bundle(self.module_id, f"{kind}-{name}", assets.tree_members(source), assets_dir)
            if asset:
                themes[kind] = {"name": name, "asset": asset}
        data["themes"] = themes
        return data

    def apply(self, data: dict, assets_dir: Path) -> None:
        # Themes first, so the keys below never point at a theme that isn't there yet.
        for kind, info in data.get("themes", {}).items():
            _schema_id, _key, user_dirs, system_dirs = THEME_KINDS.get(kind, (None, None, (), ()))
            if not user_dirs or _find(info["name"], system_dirs + user_dirs):
                continue
            target = Path(user_dirs[0]).expanduser()
            if not assets.extract_bundle(info["asset"], assets_dir, target):
                logger.warning("appearance: asset %s missing, can't install %s", info["asset"], info["name"])
        super().apply(data, assets_dir)
