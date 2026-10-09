"""Best-effort defaults for the first-run form, read from the running system."""

from __future__ import annotations

import os
import pwd
import socket

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


def _settings(schema: str) -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(schema, True) is None:
        return None
    return Gio.Settings.new(schema)


def hostname() -> str:
    return socket.gethostname()


def username() -> str:
    return os.environ.get("USER") or GLib.get_user_name()


def pretty_name() -> str:
    try:
        gecos = pwd.getpwnam(username()).pw_gecos.split(",")[0].strip()
    except KeyError:
        gecos = ""
    return gecos or GLib.get_real_name() or username()


def language() -> str:
    names = [n for n in GLib.get_language_names() if n != "C"]
    return names[0].split(".")[0] if names else "en_US"


def region() -> str:
    settings = _settings("org.gnome.system.locale")
    value = settings.get_string("region") if settings else ""
    return value.split(".")[0] if value else language()


def keyboard_layout() -> str:
    settings = _settings("org.gnome.desktop.input-sources")
    if settings is None:
        return "us"
    sources = settings.get_value("sources").unpack()
    return sources[0][1] if sources else "us"
