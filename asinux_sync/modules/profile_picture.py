from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import assets
from .base import ModuleUnavailable, SyncModule

logger = logging.getLogger(__name__)

# accountsservice refuses icons over 1 MB.
MAX_ICON_BYTES = 1024 * 1024


def _user_proxy() -> Gio.DBusProxy:
    try:
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.Accounts", f"/org/freedesktop/Accounts/User{os.getuid()}",
            "org.freedesktop.Accounts.User", None)
    except GLib.Error as exc:
        raise ModuleUnavailable(f"AccountsService not reachable: {exc.message}") from exc


def _current_icon(proxy: Gio.DBusProxy) -> Path | None:
    prop = proxy.get_cached_property("IconFile")
    path = Path(prop.get_string()) if prop is not None and prop.get_string() else Path.home() / ".face"
    return path if path.is_file() and path.stat().st_size <= MAX_ICON_BYTES else None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProfilePictureModule(SyncModule):
    module_id = "profile_picture"
    display_name = "Profile picture"
    description = "Your account picture on the login screen and in the system menu"
    category = "System"

    def capture(self, assets_dir: Path) -> dict:
        icon = _current_icon(_user_proxy())
        return {"icon": assets.store_file(self.module_id, icon, assets_dir) if icon else None}

    def apply(self, data: dict, assets_dir: Path) -> None:
        asset = data.get("icon")
        if not asset:
            return  # no picture to restore (and there is no way to "unset" one)
        restored = assets.restore_file(asset, assets_dir)
        if restored is None:
            logger.warning("profile_picture: asset %s missing, skipping", asset)
            return
        proxy = _user_proxy()
        current = _current_icon(proxy)
        if current is not None and _sha(current) == _sha(restored):
            return
        try:
            proxy.call_sync("SetIconFile", GLib.Variant("(s)", (str(restored),)), Gio.DBusCallFlags.NONE, 10000, None)
        except GLib.Error as exc:
            logger.warning("profile_picture: SetIconFile failed: %s", exc.message)
