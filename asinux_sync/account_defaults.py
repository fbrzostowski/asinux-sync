"""Defaults that come from the choices made when a profile is created."""

from __future__ import annotations

from collections.abc import Callable, Collection

from . import system_defaults


def apply_keyboard_default(
    chosen_layout: str,
    *,
    new_account: bool,
    disabled: Collection[str],
    cloud_has_keyboard: Callable[[], bool],
) -> bool:
    """Make the keyboard picked at profile creation this device's layout.

    - a new profile: always (the user has just chosen it)
    - a device joining an existing profile: only if nobody has synced a
      keyboard to the cloud yet, so it never overrides a keyboard that is
      already shared
    Does nothing if this device doesn't sync the Keyboard input sources.
    Returns True if the layout was changed.
    """
    if not chosen_layout or "keyboard" in disabled or "keyboard.input_sources" in disabled:
        return False
    if not new_account and cloud_has_keyboard():
        return False
    return system_defaults.apply_keyboard_layout(chosen_layout)
