"""Wallpaper sync - desktop (light/dark) + lock screen picture.

Scope for this module (Phase 0/1, per the architecture plan): static image
files referenced by `picture-uri`/`picture-uri-dark`/`picture-uri`
(screensaver). Dynamic-wallpaper XML dependency resolution and the
Hanabi/Damask best-effort extras from background_settings.py are explicitly
out of scope here and tracked as a later addition to this same module -
they don't change the shape of capture()/apply(), just add more keys and
more files to walk.

Same schemas as /opt/asinux-system-configuration/background_settings.py
(BACKGROUND_SCHEMA, SCREENSAVER_SCHEMA) - kept in sync deliberately so a
wallpaper set through that panel round-trips through this module unchanged.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

from .base import SyncModule

logger = logging.getLogger(__name__)

BACKGROUND_SCHEMA = "org.gnome.desktop.background"
SCREENSAVER_SCHEMA = "org.gnome.desktop.screensaver"

# (schema, key) pairs this module owns.
_BACKGROUND_KEYS = ("picture-uri", "picture-uri-dark", "picture-options")
_SCREENSAVER_KEYS = ("picture-uri",)


def _uri_to_path(uri: str) -> Path | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        # Not a local file (e.g. a resource:// built-in) - nothing to ship.
        return None
    return Path(unquote(parsed.path))


def _path_to_uri(path: Path) -> str:
    return path.as_uri()


def _strip_asset_prefix(module_id: str, name: str) -> str:
    match = re.match(rf"^{re.escape(module_id)}-[0-9a-f]{{16}}-(.+)$", name)
    return match.group(1) if match else name


class BackgroundModule(SyncModule):
    module_id = "background"
    display_name = "Wallpaper"
    description = "Desktop (light and dark) and lock screen pictures"

    def capture(self, assets_dir: Path) -> dict:
        bg = Gio.Settings.new(BACKGROUND_SCHEMA)
        screensaver = Gio.Settings.new(SCREENSAVER_SCHEMA)

        data: dict = {
            "background": {k: bg.get_string(k) for k in _BACKGROUND_KEYS},
            "screensaver": {k: screensaver.get_string(k) for k in _SCREENSAVER_KEYS},
            "assets": {},
        }

        for section, keys in (("background", ("picture-uri", "picture-uri-dark")), ("screensaver", ("picture-uri",))):
            for key in keys:
                uri = data[section][key]
                local_path = _uri_to_path(uri)
                if local_path is None or not local_path.is_file():
                    continue
                asset_name = self._store_asset(local_path, assets_dir)
                data["assets"][f"{section}.{key}"] = asset_name

        return data

    def apply(self, data: dict, assets_dir: Path) -> None:
        bg = Gio.Settings.new(BACKGROUND_SCHEMA)
        screensaver = Gio.Settings.new(SCREENSAVER_SCHEMA)
        assets = data.get("assets", {})

        for section, settings, keys in (
            ("background", bg, ("picture-uri", "picture-uri-dark", "picture-options")),
            ("screensaver", screensaver, ("picture-uri",)),
        ):
            section_data = data.get(section, {})
            for key in keys:
                if key not in section_data:
                    continue
                value = section_data[key]
                asset_name = assets.get(f"{section}.{key}")
                if asset_name:
                    restored = self._restore_asset(asset_name, assets_dir)
                    if restored is None:
                        logger.warning("background: asset %s missing, skipping %s.%s", asset_name, section, key)
                        continue
                    value = _path_to_uri(restored)
                settings.set_string(key, value)

    # -- asset helpers --------------------------------------------------

    def _store_asset(self, local_path: Path, assets_dir: Path) -> str:
        digest = hashlib.sha256(local_path.read_bytes()).hexdigest()[:16]
        # local_path may itself be a previously-restored asset (its name
        # already carries a "<module_id>-<digest>-" prefix) - strip that
        # back off first so repeated apply()/capture() cycles don't grow
        # the filename forever and stay content-addressed on the digest
        # alone.
        basename = _strip_asset_prefix(self.module_id, local_path.name)
        asset_name = f"{self.module_id}-{digest}-{basename}"
        dest = assets_dir / asset_name
        if not dest.exists():
            dest.write_bytes(local_path.read_bytes())
        return asset_name

    def _restore_asset(self, asset_name: str, assets_dir: Path) -> Path | None:
        source = assets_dir / asset_name
        if not source.is_file():
            return None
        target_dir = Path.home() / ".local" / "share" / "asinux-sync" / "assets"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / asset_name
        if not target.exists():
            target.write_bytes(source.read_bytes())
        return target
