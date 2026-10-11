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


XKB_RULES = "/usr/share/X11/xkb/rules/base.lst"


def _xkb_catalog() -> tuple[set[str], set[tuple[str, str]]] | None:
    """(layouts, (layout, variant) pairs) known to xkeyboard-config, or None."""
    try:
        lines = open(XKB_RULES, encoding="utf-8").read().splitlines()
    except OSError:
        return None
    layouts: set[str] = set()
    variants: set[tuple[str, str]] = set()
    section = None
    for line in lines:
        if line.startswith("! "):
            section = line[2:].strip()
        elif not line.strip():
            section = None
        elif section == "layout":
            layouts.add(line.split()[0])
        elif section == "variant":
            name, _, rest = line.strip().partition(" ")
            variants.add((rest.strip().split(":")[0].strip(), name))
    return layouts, variants


def valid_keyboard_layout(layout: str) -> bool:
    """Is `layout` ("pl", "us+dvorak") something the system can use?"""
    catalog = _xkb_catalog()
    if catalog is None:
        return bool(layout.strip())  # can't check: don't block the user
    layouts, variants = catalog
    base, _, variant = layout.strip().partition("+")
    return base in layouts and (not variant or (base, variant) in variants)


def apply_keyboard_layout(layout: str) -> bool:
    """Make `layout` the only input source, unless it is the first one already.
    Returns True if anything changed."""
    settings = _settings("org.gnome.desktop.input-sources")
    if settings is None or not valid_keyboard_layout(layout):
        return False
    layout = layout.strip()
    current = settings.get_value("sources").unpack()
    if current and current[0] == ("xkb", layout):
        return False
    settings.set_value("sources", GLib.Variant("a(ss)", [("xkb", layout)]))
    Gio.Settings.sync()
    return True
