"""Wallpaper sync: desktop (light/dark) and lock screen pictures, the user's
own dynamic-wallpaper XMLs (with the images they reference), the Hanabi live
wallpaper (settings + video) and Damask's settings.

Same schemas as /opt/asinux-system-configuration/background_settings.py,
kept in step deliberately so a wallpaper set through that panel round-trips
through this module unchanged. Files that ship with the distribution
(/usr/...) are referenced, not uploaded - every ASinux machine has them.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from . import assets, gsettings_util
from .base import SyncModule

logger = logging.getLogger(__name__)

BACKGROUND_SCHEMA = "org.gnome.desktop.background"
SCREENSAVER_SCHEMA = "org.gnome.desktop.screensaver"
HANABI_SCHEMA = "io.github.jeffshee.hanabi-extension"
# GPU/driver specific: what works on one machine can break another.
HANABI_LOCAL_ONLY = {"debug-mode", "enable-graphics-offload", "enable-nvsl", "enable-va", "prefer-clappersink"}
DAMASK_APP_DIR = Path("~/.var/app/app.drey.Damask").expanduser()
DAMASK_KEYFILE = DAMASK_APP_DIR / "config/glib-2.0/settings/keyfile"

_BACKGROUND_KEYS = ("picture-uri", "picture-uri-dark", "picture-options")
_SCREENSAVER_KEYS = ("picture-uri",)
_BUNDLE_PLACEHOLDER = "@BUNDLE@"


def _uri_to_path(uri: str) -> Path | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        return None  # not a local file (e.g. a resource:// built-in)
    return Path(unquote(parsed.path))


def _hanabi_keys() -> tuple[str, ...]:
    found = gsettings_util.schema(HANABI_SCHEMA)
    return tuple(k for k in found.list_keys() if k not in HANABI_LOCAL_ONLY) if found else ()


class BackgroundModule(SyncModule):
    module_id = "background"
    display_name = "Wallpaper"
    description = "Desktop and lock screen pictures, your dynamic wallpapers, live wallpaper (Hanabi), Damask"
    category = "Look and feel"

    # -- capture ----------------------------------------------------------

    def capture(self, assets_dir: Path) -> dict:
        data: dict = {
            "background": {k: gsettings_util.get_string(BACKGROUND_SCHEMA, k) for k in _BACKGROUND_KEYS},
            "screensaver": {k: gsettings_util.get_string(SCREENSAVER_SCHEMA, k) for k in _SCREENSAVER_KEYS},
            "assets": {},
            "bundles": {},
        }
        for section, keys in (("background", ("picture-uri", "picture-uri-dark")), ("screensaver", ("picture-uri",))):
            for key in keys:
                path = _uri_to_path(data[section][key])
                if path is None or assets.is_system_path(path) or not path.is_file():
                    continue
                if path.suffix.lower() == ".xml":
                    bundle = self._store_dynamic_wallpaper(path, assets_dir)
                    if bundle:
                        data["bundles"][f"{section}.{key}"] = bundle
                else:
                    asset = assets.store_file(self.module_id, path, assets_dir)
                    if asset:
                        data["assets"][f"{section}.{key}"] = asset

        hanabi = gsettings_util.capture_keys(HANABI_SCHEMA, _hanabi_keys())
        if hanabi is not None:
            video = gsettings_util.get_string(HANABI_SCHEMA, "video-path")
            video_asset = assets.store_file(self.module_id, Path(video), assets_dir) if video else None
            data["hanabi"] = {"values": hanabi, "video": video_asset}

        if DAMASK_KEYFILE.is_file():
            data["damask"] = DAMASK_KEYFILE.read_text(encoding="utf-8")
        return data

    def _store_dynamic_wallpaper(self, xml_path: Path, assets_dir: Path) -> str | None:
        """Bundle a user dynamic-wallpaper XML with every image it references.
        Paths inside are rewritten to a placeholder, so the bundle is the same
        on every machine - and the same again after being restored."""
        try:
            text = xml_path.read_text(encoding="utf-8")
            root = ET.fromstring(text)
        except (OSError, ET.ParseError, UnicodeDecodeError):
            return None
        members: dict[str, assets.Member] = {}
        replacements: dict[str, str] = {}
        for element in root.iter():
            if element.tag not in ("file", "from", "to") or not (element.text or "").strip():
                continue
            original = element.text.strip()
            image = Path(original) if original.startswith("/") else xml_path.parent / original
            if not image.is_file() or assets.is_system_path(image):
                continue
            digest = hashlib.sha256(image.read_bytes()).hexdigest()[:12]
            name = f"{digest}-{re.sub(r'^[0-9a-f]{12}-', '', image.name)}"
            members[f"images/{name}"] = image
            replacements[original] = f"{_BUNDLE_PLACEHOLDER}/images/{name}"
        for original, placeholder in replacements.items():
            text = text.replace(f">{original}<", f">{placeholder}<")
        members["wallpaper.xml"] = text.encode("utf-8")
        return assets.store_bundle(self.module_id, "wallpaper", members, assets_dir, cache_key=str(xml_path))

    # -- apply ------------------------------------------------------------

    def apply(self, data: dict, assets_dir: Path) -> None:
        for section, schema_id, keys in (
            ("background", BACKGROUND_SCHEMA, _BACKGROUND_KEYS),
            ("screensaver", SCREENSAVER_SCHEMA, _SCREENSAVER_KEYS),
        ):
            values = {}
            for key in keys:
                if key not in data.get(section, {}):
                    continue
                value = data[section][key]
                if key.endswith("uri"):
                    restored = self._restore(f"{section}.{key}", data, assets_dir)
                    if restored is not None:
                        value = restored.as_uri()
                    else:
                        path = _uri_to_path(value)
                        if path is not None and not path.exists():
                            logger.warning("background: %s is missing here, leaving %s.%s alone", path, section, key)
                            continue
                values[key] = GLib.Variant("s", value).print_(False)
            self._apply_present(schema_id, values)

        if "hanabi" in data:
            self._apply_hanabi(data["hanabi"], assets_dir)
        if "damask" in data and DAMASK_APP_DIR.is_dir():
            self._write_if_different(DAMASK_KEYFILE, data["damask"])

    def _restore(self, slot: str, data: dict, assets_dir: Path) -> Path | None:
        if slot in data.get("bundles", {}):
            name = data["bundles"][slot]
            target = assets.assets_home() / "wallpapers" / name.removesuffix(".tar.gz")
            xml_file = target / "wallpaper.xml"
            if not xml_file.exists():
                if not assets.extract_bundle(name, assets_dir, target):
                    logger.warning("background: bundle %s missing, skipping %s", name, slot)
                    return None
                xml_file.write_text(xml_file.read_text(encoding="utf-8").replace(_BUNDLE_PLACEHOLDER, str(target)), encoding="utf-8")
            return xml_file
        if slot in data.get("assets", {}):
            restored = assets.restore_file(data["assets"][slot], assets_dir)
            if restored is None:
                logger.warning("background: asset %s missing, skipping %s", data["assets"][slot], slot)
            return restored
        return None

    @staticmethod
    def _apply_present(schema_id: str, values: dict[str, str]) -> None:
        # Only the keys this profile carries: unlike the allowlist modules,
        # nothing here should be reset to default.
        for key, text in values.items():
            gsettings_util.apply_keys(schema_id, (key,), {key: text})

    def _apply_hanabi(self, hanabi: dict, assets_dir: Path) -> None:
        values = dict(hanabi.get("values", {}))
        skip = set()
        video = hanabi.get("video")
        if video:
            restored = assets.restore_file(video, assets_dir)
            if restored is not None:
                values["video-path"] = GLib.Variant("s", str(restored)).print_(False)
            else:
                skip.add("video-path")
        elif "video-path" in values:
            skip.add("video-path")  # a path on the other machine, with no file to go with it
        gsettings_util.apply_keys(HANABI_SCHEMA, _hanabi_keys(), values, skip=skip)

    @staticmethod
    def _write_if_different(path: Path, text: str) -> None:
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
