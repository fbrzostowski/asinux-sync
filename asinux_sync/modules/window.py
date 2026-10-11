from .gsettings_module import Group, GSettingsModule

WM = "org.gnome.desktop.wm.preferences"


class WindowModule(GSettingsModule):
    module_id = "window"
    display_name = "Window behavior"
    description = "How windows look and react"
    category = "Look and feel"

    # Same schemas as /opt/asinux-system-configuration/window_settings.py.
    groups = (
        Group("titlebar_buttons", "Titlebar buttons", "Which buttons the titlebar has and on which side",
              {WM: ("button-layout",)}),
        Group("titlebar_actions", "Titlebar clicks", "Double, middle and right click on a titlebar",
              {WM: ("action-double-click-titlebar", "action-middle-click-titlebar", "action-right-click-titlebar")}),
        Group("focus", "Focus", "Focus mode, raising and focus for new windows",
              {WM: ("focus-mode", "focus-new-windows", "auto-raise", "auto-raise-delay", "raise-on-click")}),
        Group("mouse_modifier", "Window modifier key", "The key for moving and resizing windows with the mouse",
              {WM: ("mouse-button-modifier", "resize-with-right-button")}),
        Group("placement", "New windows", "Placement of new windows and modal dialogs",
              {"org.gnome.mutter": ("attach-modal-dialogs", "center-new-windows")}),
    )
