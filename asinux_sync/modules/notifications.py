from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

try:  # newer GLib moved DesktopAppInfo to GioUnix and deprecated the Gio one
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix

    DesktopAppInfo = GioUnix.DesktopAppInfo
except (ValueError, ImportError):
    DesktopAppInfo = Gio.DesktopAppInfo

from . import gsettings_util
from .base import Item
from .gsettings_module import Group, GSettingsModule

MASTER = "org.gnome.desktop.notifications"
APP_SCHEMA = f"{MASTER}.application"
APP_PREFIX = "/org/gnome/desktop/notifications/application/"
# The six switches of an app's page in gnome-control-center's Notifications panel.
APP_KEYS = (
    "enable", "enable-sound-alerts", "show-banners", "force-expanded",
    "show-in-lock-screen", "details-in-lock-screen",
)
APP = "app:"


def _desktop_info(application_id: str):
    """The app's .desktop info, or None. Gio raises (instead of returning
    None) for an app that has been uninstalled but still has settings."""
    if not application_id:
        return None
    try:
        return DesktopAppInfo.new(application_id)
    except TypeError:
        return None


def _app_name(child: str, application_id: str) -> str:
    info = _desktop_info(application_id)
    return info.get_display_name() if info is not None else child


def _app_installed(application_id: str) -> bool:
    return _desktop_info(application_id) is not None


class NotificationsModule(GSettingsModule):
    module_id = "notifications"
    display_name = "Notifications"
    description = "Do Not Disturb, lock screen notifications and the settings of each app"
    category = "System"

    # Mirrors gnome-control-center's Notifications panel (checked against
    # panels/notifications/cc-notifications-panel.c and
    # cc-app-notifications-page.c): the two switches at the top, then one
    # item per app with its six switches.
    groups = (
        Group("do_not_disturb", "Do not disturb", "Show notification banners (off = Do Not Disturb)",
              {MASTER: ("show-banners",)}),
        Group("lock_screen", "Lock screen notifications", "Show notifications on the lock screen",
              {MASTER: ("show-in-lock-screen",)}),
    )

    def _children(self) -> list[str]:
        return gsettings_util.get_strv(MASTER, "application-children")

    def list_items(self) -> list[Item]:
        apps = []
        for child in self._children():
            application_id = gsettings_util.get_string(APP_SCHEMA, "application-id", APP_PREFIX + child + "/")
            apps.append(Item(APP + child, _app_name(child, application_id), application_id))
        apps.sort(key=lambda item: item.name.lower())
        return super().list_items() + apps

    def is_default(self, item_id: str, data: dict) -> bool:
        if item_id.startswith(APP):
            return not data.get("values")
        return super().is_default(item_id, data)

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        if not item_id.startswith(APP):
            return super().capture_item(item_id, assets_dir)
        path = APP_PREFIX + item_id.removeprefix(APP) + "/"
        return {
            "application_id": gsettings_util.get_string(APP_SCHEMA, "application-id", path),
            "values": gsettings_util.capture_keys(APP_SCHEMA, APP_KEYS, path) or {},
        }

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        if not item_id.startswith(APP):
            return super().apply_item(item_id, data, assets_dir)
        child = item_id.removeprefix(APP)
        path = APP_PREFIX + child + "/"
        children = self._children()
        application_id = data.get("application_id", "")
        if child not in children:
            if not _app_installed(application_id):
                return  # an app this machine doesn't have: nothing to configure
            # Installed but never notified yet: register it so the settings apply.
            gsettings_util.set_strv(MASTER, "application-children", children + [child])
            gsettings_util.apply_keys(
                APP_SCHEMA, ("application-id",),
                {"application-id": GLib.Variant("s", application_id).print_(False)}, path)
        gsettings_util.apply_keys(APP_SCHEMA, APP_KEYS, data.get("values", {}), path)

    def apply(self, data: dict, assets_dir: Path, disabled_items=frozenset()) -> None:
        super().apply(data, assets_dir, disabled_items)
        # Apps that aren't in this machine's list yet can still be set up if installed.
        known = {item.id for item in self.list_items()}
        for item_id, item_data in data.get("items", {}).items():
            if item_id.startswith(APP) and item_id not in known and item_id not in disabled_items:
                self.apply_item(item_id, item_data, assets_dir)
