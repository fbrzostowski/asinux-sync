"""Extensions that are on in the cloud profile but not installed on this computer.

Nothing is installed behind the user's back: the background service asks
(asinux_sync_settings/extension_prompt.py), and what the user declines is
remembered in Config.declined_extensions so it is never asked again here.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PREFS = "#prefs"


def find_missing(
    profile: dict | None,
    disabled: Collection[str],
    declined: Collection[str],
    installed: Collection[str],
) -> list[str]:
    """UUIDs turned on in the cloud profile that this computer should be asked about.

    Left out: extensions already installed, ones the user declined here, and
    everything when the Extensions category (or that one extension) is
    switched off on this computer - then it isn't syncing them anyway."""
    if not profile or profile.get("version", 1) < 2 or "extensions" in disabled:
        return []
    items = profile.get("modules", {}).get("extensions", {}).get("items", {})
    return sorted(
        uuid
        for uuid, data in items.items()
        if not uuid.endswith(PREFS)
        and data.get("enabled")
        and uuid not in installed
        and uuid not in declined
        and f"extensions.{uuid}" not in disabled
    )


def names_from_devices(devices: Iterable[dict]) -> dict[str, str]:
    """uuid -> display name, from the item catalogs the devices publish."""
    names: dict[str, str] = {}
    for device in devices:
        for entry in device.get("catalog", {}).get("extensions", []):
            if not entry.get("hidden") and entry.get("name"):
                names.setdefault(entry["id"], entry["name"])
    return names


def install_remote(uuid: str) -> str:
    """Install one extension from extensions.gnome.org through GNOME Shell.

    GNOME Shell shows its own "Download and install?" confirmation and this
    waits for the answer. Returns "successful", "already-installed",
    "cancelled" or "error: <reason>"."""
    try:
        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
            "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions", None)
        reply = proxy.call_sync("InstallRemoteExtension", GLib.Variant("(s)", (uuid,)), Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as exc:
        return f"error: {exc.message}"
    return reply.unpack()[0] or "error: no answer"


def build_result(requested: Iterable[str], selected: Collection[str], outcomes: dict[str, str]) -> dict:
    """What the user decided, in the form the service records.

    - installed: it is there now (so its cloud settings should be applied)
    - declined: not wanted here - unchecked, cancelled in GNOME Shell, or not
      installable (not on extensions.gnome.org) - and not to be asked about again
    - failed: why the ones that were wanted but couldn't be installed failed"""
    installed, declined, failed = [], [], {}
    for uuid in requested:
        if uuid not in selected:
            declined.append(uuid)
            continue
        outcome = outcomes.get(uuid, "error: not attempted")
        if outcome in ("successful", "already-installed"):
            installed.append(uuid)
        elif outcome == "cancelled":
            declined.append(uuid)
        else:
            declined.append(uuid)
            failed[uuid] = outcome
    return {"installed": installed, "declined": declined, "failed": failed}
