"""Allowlist-driven GSettings capture/apply shared by the sync modules.

Only values the user has explicitly set are captured (`get_user_value`), so a
profile stays small and "this key is at its default here" propagates as a
reset on the other devices. Schemas or keys missing on a machine (an
extension that isn't installed, an older GNOME) are skipped, never errors.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

logger = logging.getLogger(__name__)


def schema(schema_id: str) -> Gio.SettingsSchema | None:
    source = Gio.SettingsSchemaSource.get_default()
    return source.lookup(schema_id, True) if source else None


def settings(schema_id: str, path: str | None = None) -> Gio.Settings | None:
    found = schema(schema_id)
    return Gio.Settings.new_full(found, None, path) if found else None


def get_string(schema_id: str, key: str, path: str | None = None) -> str:
    handle = settings(schema_id, path)
    if handle is None or not schema(schema_id).has_key(key):
        return ""
    return handle.get_string(key)


def get_strv(schema_id: str, key: str) -> list[str]:
    handle = settings(schema_id)
    if handle is None or not schema(schema_id).has_key(key):
        return []
    return list(handle.get_strv(key))


def set_strv(schema_id: str, key: str, values: list[str]) -> None:
    handle = settings(schema_id)
    if handle is not None and schema(schema_id).has_key(key) and list(handle.get_strv(key)) != values:
        handle.set_strv(key, values)


def capture_keys(schema_id: str, keys, path: str | None = None) -> dict[str, str] | None:
    """User-set values as GVariant text, or None if the schema isn't here."""
    found = schema(schema_id)
    if found is None:
        return None
    handle = Gio.Settings.new_full(found, None, path)
    values = {}
    for key in keys:
        if not found.has_key(key):
            continue
        value = handle.get_user_value(key)
        if value is not None:
            values[key] = value.print_(False)
    return values


def apply_keys(schema_id: str, keys, values: dict[str, str], path: str | None = None, skip=()) -> None:
    """Make the listed keys match `values`: set the ones present, reset the
    rest. Writes only what differs, so an unchanged profile touches nothing
    (no needless dconf writes or wallpaper flicker)."""
    found = schema(schema_id)
    if found is None:
        return
    handle = Gio.Settings.new_full(found, None, path)
    for key in keys:
        if key in skip or not found.has_key(key):
            continue
        current = handle.get_user_value(key)
        if key in values:
            schema_key = found.get_key(key)
            try:
                wanted = GLib.Variant.parse(schema_key.get_value_type(), values[key], None, None)
            except GLib.Error:
                logger.warning("%s.%s: unparsable value %r, skipping", schema_id, key, values[key])
                continue
            if not schema_key.range_check(wanted):
                logger.warning("%s.%s: value out of range here, skipping", schema_id, key)
                continue
            if current is None or not current.equal(wanted):
                handle.set_value(key, wanted)
        elif current is not None:
            handle.reset(key)
    Gio.Settings.sync()
