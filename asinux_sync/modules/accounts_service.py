"""The current user's AccountsService object on the system bus."""

from __future__ import annotations

import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .base import ModuleUnavailable


def user_proxy() -> Gio.DBusProxy:
    try:
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.Accounts", f"/org/freedesktop/Accounts/User{os.getuid()}",
            "org.freedesktop.Accounts.User", None)
    except GLib.Error as exc:
        raise ModuleUnavailable(f"AccountsService not reachable: {exc.message}") from exc
