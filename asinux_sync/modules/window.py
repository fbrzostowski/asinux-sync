from .gsettings_module import GSettingsModule


class WindowModule(GSettingsModule):
    module_id = "window"
    display_name = "Window behavior"
    description = "Titlebar buttons and actions, focus mode, new window placement"
    category = "Look and feel"

    # Same schemas as /opt/asinux-system-configuration/window_settings.py.
    schemas = {
        "org.gnome.desktop.wm.preferences": (
            "button-layout",
            "action-double-click-titlebar",
            "action-middle-click-titlebar",
            "action-right-click-titlebar",
            "focus-mode",
            "focus-new-windows",
            "auto-raise",
            "auto-raise-delay",
            "raise-on-click",
            "mouse-button-modifier",
            "resize-with-right-button",
        ),
        "org.gnome.mutter": ("attach-modal-dialogs", "center-new-windows"),
    }
