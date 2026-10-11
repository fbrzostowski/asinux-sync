from __future__ import annotations

import gi

from .gsettings_module import Group, GSettingsModule

MOUSE = "org.gnome.desktop.peripherals.mouse"
TOUCHPAD = "org.gnome.desktop.peripherals.touchpad"
POINTING_STICK = "org.gnome.desktop.peripherals.pointingstick"


def _has_input_device(udev_property: str) -> bool:
    """Is there such a device? Same GUdev check gnome-control-center uses to
    decide whether to show its Touchpad / Pointing Stick tabs."""
    try:
        gi.require_version("GUdev", "1.0")
        from gi.repository import GUdev
    except (ValueError, ImportError):
        return True  # can't tell: better to offer the items than to hide them
    devices = GUdev.Client.new(["input"]).query_by_subsystem("input")
    return any(device.get_property(udev_property) for device in devices)


class MouseModule(GSettingsModule):
    module_id = "mouse"
    display_name = "Mouse and touchpad"
    description = "Pointer speed, acceleration, scrolling and clicking"
    category = "Input"

    # One item per row of gnome-control-center's Mouse & Touchpad panel
    # (panels/mouse/cc-mouse-panel.blp/.c), plus the middle-click paste and
    # pointing stick scrolling rows that system-configuration's Mouse panel
    # adds from GNOME Tweaks.
    #
    # Touchpad and pointing stick items exist only on machines that have the
    # hardware. Otherwise a desktop with nothing set would reset a laptop's
    # touchpad to defaults.
    MOUSE_GROUPS = (
        Group("mouse_primary_button", "Primary button", "Order of physical buttons on mice and touchpads",
              {MOUSE: ("left-handed",)}),
        Group("mouse_speed", "Mouse pointer speed", "", {MOUSE: ("speed",)}),
        Group("mouse_acceleration", "Mouse acceleration", "Recommended for most users and applications",
              {MOUSE: ("accel-profile",)}),
        Group("mouse_scroll_direction", "Mouse scroll direction", "Traditional or natural scrolling",
              {MOUSE: ("natural-scroll",)}),
        Group("middle_click_paste", "Middle click paste", "Paste the selection with a middle click",
              {"org.gnome.desktop.interface": ("gtk-enable-primary-paste",)}),
    )
    TOUCHPAD_GROUPS = (
        Group("touchpad_enabled", "Touchpad on or off", "", {TOUCHPAD: ("send-events",)}),
        Group("touchpad_disable_while_typing", "Disable touchpad while typing", "",
              {TOUCHPAD: ("disable-while-typing",)}),
        Group("touchpad_speed", "Touchpad pointer speed", "", {TOUCHPAD: ("speed",)}),
        Group("touchpad_secondary_click", "Touchpad secondary click", "Two finger push or corner push",
              {TOUCHPAD: ("click-method",)}),
        Group("touchpad_tap_to_click", "Tap to click", "Quickly touch the touchpad to click",
              {TOUCHPAD: ("tap-to-click",)}),
        Group("touchpad_scroll_method", "Touchpad scroll method", "Two finger or edge scrolling",
              {TOUCHPAD: ("two-finger-scrolling-enabled", "edge-scrolling-enabled")}),
        Group("touchpad_scroll_direction", "Touchpad scroll direction", "Traditional or natural scrolling",
              {TOUCHPAD: ("natural-scroll",)}),
    )
    POINTING_STICK_GROUPS = (
        Group("stick_speed", "Pointing stick speed", "", {POINTING_STICK: ("speed",)}),
        Group("stick_acceleration", "Pointing stick acceleration", "Recommended for most users and applications",
              {POINTING_STICK: ("accel-profile",)}),
        Group("stick_scroll_method", "Pointing stick scroll method", "", {POINTING_STICK: ("scroll-method",)}),
    )

    def group_specs(self):
        # Asked of udev once per module instance (the service makes a fresh
        # one every cycle), not once per item.
        if getattr(self, "_specs", None) is None:
            specs = self.MOUSE_GROUPS
            if _has_input_device("ID_INPUT_TOUCHPAD"):
                specs += self.TOUCHPAD_GROUPS
            if _has_input_device("ID_INPUT_POINTINGSTICK"):
                specs += self.POINTING_STICK_GROUPS
            self._specs = specs
        return self._specs
