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
from .base import Item, SyncModule

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


# item id -> (schema, keys). The URI keys are the ones that can point at a file.
PICTURES = {
    "desktop": (BACKGROUND_SCHEMA, _BACKGROUND_KEYS),
    "lockscreen": (SCREENSAVER_SCHEMA, _SCREENSAVER_KEYS),
}


class BackgroundModule(SyncModule):
    module_id = "background"
    display_name = "Wallpaper"
    description = "Desktop and lock screen pictures, live wallpaper, automatic wallpapers"
    category = "Look and feel"

    def list_items(self) -> list[Item]:
        items = [
            Item("desktop", "Desktop wallpaper", "Light and dark picture and how it fits, including your own dynamic wallpapers"),
            Item("lockscreen", "Lock screen picture"),
        ]
        if gsettings_util.schema(HANABI_SCHEMA) is not None:
            items.append(Item("live_wallpaper", "Live wallpaper (Hanabi)", "Video, volume, fit and pause rules"))
        if DAMASK_APP_DIR.is_dir():
            items.append(Item("damask", "Automatic wallpapers (Damask)", "Which source Damask uses"))
        return items

    # -- capture ----------------------------------------------------------

    def is_default(self, item_id: str, data: dict) -> bool:
        if item_id in PICTURES:
            return not data.get("customised", True)
        if item_id == "live_wallpaper":
            return not any(data.get("values", {}).values()) and not data.get("video")
        return False

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        if item_id in PICTURES:
            return self._capture_picture(*PICTURES[item_id], assets_dir)
        if item_id == "live_wallpaper":
            hanabi = gsettings_util.capture_keys(HANABI_SCHEMA, _hanabi_keys())
            if hanabi is None:
                return None
            video = gsettings_util.get_string(HANABI_SCHEMA, "video-path")
            video_asset = assets.store_file(self.module_id, Path(video), assets_dir) if video else None
            return {"values": hanabi, "video": video_asset}
        if item_id == "damask" and DAMASK_KEYFILE.is_file():
            return {"keyfile": DAMASK_KEYFILE.read_text(encoding="utf-8")}
        return None

    def _capture_picture(self, schema_id: str, keys: tuple[str, ...], assets_dir: Path) -> dict:
        data: dict = {
            "values": {k: gsettings_util.get_string(schema_id, k) for k in keys},
            # The values above are effective ones (defaults included); this says
            # whether the user ever set any of them.
            "customised": bool(gsettings_util.capture_keys(schema_id, keys)),
            "assets": {},
            "bundles": {},
        }
        for key in keys:
            if not key.endswith("uri"):
                continue
            path = _uri_to_path(data["values"][key])
            if path is None or assets.is_system_path(path) or not path.is_file():
                continue
            if path.suffix.lower() == ".xml":
                bundle = self._store_dynamic_wallpaper(path, assets_dir)
                if bundle:
                    data["bundles"][key] = bundle
            else:
                asset = assets.store_file(self.module_id, path, assets_dir)
                if asset:
                    data["assets"][key] = asset
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

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        if item_id in PICTURES:
            self._apply_picture(*PICTURES[item_id], data, assets_dir)
        elif item_id == "live_wallpaper":
            self._apply_hanabi(data, assets_dir)
        elif item_id == "damask" and DAMASK_APP_DIR.is_dir():
            self._write_if_different(DAMASK_KEYFILE, data["keyfile"])

    def _apply_picture(self, schema_id: str, keys: tuple[str, ...], data: dict, assets_dir: Path) -> None:
        for key in keys:
            if key not in data.get("values", {}):
                continue
            value = data["values"][key]
            if key.endswith("uri"):
                restored = self._restore(key, data, assets_dir)
                if restored is not None:
                    value = restored.as_uri()
                else:
                    path = _uri_to_path(value)
                    if path is not None and not path.exists():
                        logger.warning("background: %s is missing here, leaving %s alone", path, key)
                        continue
            # Only the keys this profile carries: nothing here is reset to default.
            gsettings_util.apply_keys(schema_id, (key,), {key: GLib.Variant("s", value).print_(False)})

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
