from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import gsettings_util
from .accounts_service import user_proxy as _user_proxy
from .base import Item, SyncModule

logger = logging.getLogger(__name__)

LOCALE = "org.gnome.system.locale"


def _locale_installed(name: str) -> bool:
    """Is this locale generated here? (Setting a missing one would leave the
    account with a language it can't actually use.)"""
    try:
        listed = subprocess.run(["locale", "-a"], capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return True  # can't tell: trust the profile
    installed = {entry.lower() for entry in listed}
    wanted = name.lower()
    return not installed or wanted in installed or wanted.replace("utf-8", "utf8") in installed


class RegionModule(SyncModule):
    module_id = "region"
    display_name = "Region and language"
    description = "Your account's language and formats. Applied after the next login"
    category = "System"

    # The "Your Account" half of gnome-control-center's Region & Language page
    # (panels/system/region/cc-region-page.c): the language and formats of
    # this user, stored as AccountsService `Languages` = [language, formats]
    # and the `region` key. The "Login Screen" half is system-wide (localed),
    # needs an administrator, and isn't synced.
    def list_items(self) -> list[Item]:
        return [
            Item("language", "Language", "Language for interface text and web pages"),
            Item("formats", "Formats", "Numbers, dates and currencies"),
        ]

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        if item_id == "formats":
            return {"region": gsettings_util.get_string(LOCALE, "region")}
        languages = self._languages()
        return {"language": languages[0]} if languages else None

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        if item_id == "formats":
            region = data.get("region", "")
            if region and not _locale_installed(region):
                logger.warning("region: formats locale %s isn't installed here, skipping", region)
                return
            handle = gsettings_util.settings(LOCALE)
            if handle is not None:
                handle.set_string("region", region) if region else handle.reset("region")
            languages = self._languages()
            self._set_languages(languages[0] if languages else "", region)
            return
        language = data.get("language", "")
        if not language or not _locale_installed(language):
            logger.warning("region: language %s isn't installed here, skipping", language)
            return
        self._set_languages(language, gsettings_util.get_string(LOCALE, "region"))

    # -- AccountsService ----------------------------------------------------

    @staticmethod
    def _languages() -> list[str]:
        prop = _user_proxy().get_cached_property("Languages")
        return list(prop.unpack()) if prop is not None else []

    @staticmethod
    def _set_languages(language: str, region: str) -> None:
        """What the panel stores: [language] or [language, formats]."""
        if not language:
            return
        wanted = [language] + ([region] if region and region != language else [])
        proxy = _user_proxy()
        prop = proxy.get_cached_property("Languages")
        if prop is not None and list(prop.unpack()) == wanted:
            return
        try:
            proxy.call_sync("SetLanguages", GLib.Variant("(as)", (wanted,)), Gio.DBusCallFlags.NONE, 10000, None)
        except GLib.Error as exc:
            logger.warning("region: SetLanguages failed: %s", exc.message)
