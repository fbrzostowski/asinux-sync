"""Allowlist-driven GSettings capture/apply shared by the sync modules.

Only values the user has explicitly set are captured (`get_user_value`), so a
profile stays small and "this key is at its default here" propagates as a
reset on the other devices. Schemas or keys missing on a machine (an
extension that isn't installed, an older GNOME) are skipped, never errors.
"""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

logger = logging.getLogger(__name__)


def _extension_roots() -> list[Path]:
    return [Path(GLib.get_user_data_dir()) / "gnome-shell" / "extensions"] + [
        Path(d) / "gnome-shell" / "extensions" for d in GLib.get_system_data_dirs()]


# schemas dir -> (mtime of its gschemas.compiled, the source read from it)
_sources: dict[str, tuple[float, Gio.SettingsSchemaSource]] = {}


def _extension_sources():
    """Schema sources of installed GNOME Shell extensions. An extension
    installed in the user's home (e.g. User Themes from extensions.gnome.org)
    ships its schema in its own folder, where GSettings doesn't look."""
    default = Gio.SettingsSchemaSource.get_default()
    for root in _extension_roots():
        for folder in sorted(root.glob("*/schemas")):
            compiled = folder / "gschemas.compiled"
            try:
                mtime = compiled.stat().st_mtime
            except OSError:
                continue
            cached = _sources.get(str(folder))
            if cached is None or cached[0] != mtime:
                try:
                    cached = (mtime, Gio.SettingsSchemaSource.new_from_directory(str(folder), default, False))
                except GLib.Error:
                    continue
                _sources[str(folder)] = cached
            yield cached[1]


def schema(schema_id: str) -> Gio.SettingsSchema | None:
    source = Gio.SettingsSchemaSource.get_default()
    found = source.lookup(schema_id, True) if source else None
    if found is None:
        for extension_source in _extension_sources():
            found = extension_source.lookup(schema_id, False)
            if found is not None:
                break
    return found


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
