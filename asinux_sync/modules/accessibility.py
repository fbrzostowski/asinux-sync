from .gsettings_module import Group, GSettingsModule

A11Y = "org.gnome.desktop.a11y"
A11Y_INTERFACE = f"{A11Y}.interface"
A11Y_APPS = f"{A11Y}.applications"
A11Y_KEYBOARD = f"{A11Y}.keyboard"
A11Y_MOUSE = f"{A11Y}.mouse"
MAGNIFIER = f"{A11Y}.magnifier"
INTERFACE = "org.gnome.desktop.interface"


class AccessibilityModule(GSettingsModule):
    module_id = "accessibility"
    display_name = "Accessibility"
    description = "Seeing, hearing, typing, pointing and zoom"
    category = "Accessibility"

    # One item per row of gnome-control-center's Accessibility panel, taken
    # from panels/universal-access (cc-ua-macros.h and the five pages).
    # Deliberately left out because another module already owns the key:
    # Cursor Size (Theme, icons and cursor), Repeat Keys (Keyboard) and
    # Activate Windows on Hover, which is the window focus mode (Window behavior).
    groups = (
        Group("menu", "Accessibility menu", "Always show the accessibility menu in the top bar",
              {A11Y: ("always-show-universal-access-status",)}),
        # -- Seeing
        Group("screen_reader", "Screen reader", "Read displayed text as the focus moves",
              {A11Y_APPS: ("screen-reader-enabled",)}),
        Group("high_contrast", "High contrast", "Increase the contrast of interface elements",
              {A11Y_INTERFACE: ("high-contrast",)}),
        Group("status_shapes", "On/off shapes", "Use shapes to show state as well as color",
              {A11Y_INTERFACE: ("show-status-shapes",)}),
        Group("reduced_motion", "Reduced motion", "Reduce animations across the interface",
              {A11Y_INTERFACE: ("reduced-motion",)}),
        Group("text_size", "Text size", "Large text", {INTERFACE: ("text-scaling-factor",)}),
        Group("sound_keys", "Sound keys", "Beep when Num Lock or Caps Lock is toggled",
              {A11Y_KEYBOARD: ("togglekeys-enable",)}),
        Group("scrollbars", "Always show scrollbars", "Make scrollbars always visible",
              {INTERFACE: ("overlay-scrolling",)}),
        # -- Hearing
        Group("overamplification", "Overamplification", "Allow volume above 100%",
              {"org.gnome.desktop.sound": ("allow-volume-above-100-percent",)}),
        Group("visual_alerts", "Visual alerts", "Flash the screen or window instead of beeping",
              {"org.gnome.desktop.wm.preferences": ("visual-bell", "visual-bell-type")}),
        # -- Typing
        Group("screen_keyboard", "Screen keyboard", "Use the on-screen keyboard to type",
              {A11Y_APPS: ("screen-keyboard-enabled",)}),
        Group("enable_by_keyboard", "Enable by keyboard", "Turn accessibility features on or off with the keyboard",
              {A11Y_KEYBOARD: ("enable",)}),
        Group("text_cursor", "Text cursor blinking", "Whether the text cursor blinks, and how fast",
              {INTERFACE: ("cursor-blink", "cursor-blink-time")}),
        Group("sticky_keys", "Sticky keys", "Sequences of modifier keys act as a key combination",
              {A11Y_KEYBOARD: ("stickykeys-enable", "stickykeys-two-key-off", "stickykeys-modifier-beep")}),
        Group("slow_keys", "Slow keys", "Delay between pressing a key and it being accepted",
              {A11Y_KEYBOARD: ("slowkeys-enable", "slowkeys-delay", "slowkeys-beep-press",
                               "slowkeys-beep-accept", "slowkeys-beep-reject")}),
        Group("bounce_keys", "Bounce keys", "Ignore fast duplicate key presses",
              {A11Y_KEYBOARD: ("bouncekeys-enable", "bouncekeys-delay", "bouncekeys-beep-reject")}),
        # -- Pointing and clicking
        Group("mouse_keys", "Mouse keys", "Move the pointer with the numeric keypad",
              {A11Y_KEYBOARD: ("mousekeys-enable",)}),
        Group("locate_pointer", "Locate pointer", "Reveal the pointer by pressing Left Ctrl",
              {INTERFACE: ("locate-pointer",)}),
        Group("double_click_delay", "Double-click delay", "",
              {"org.gnome.desktop.peripherals.mouse": ("double-click",)}),
        Group("secondary_click", "Simulated secondary click", "Hold the primary button to right-click",
              {A11Y_MOUSE: ("secondary-click-enabled", "secondary-click-time")}),
        Group("hover_click", "Hover click", "Click when the pointer hovers",
              {A11Y_MOUSE: ("dwell-click-enabled", "dwell-time", "dwell-threshold")}),
        # -- Zoom
        Group("zoom", "Desktop zoom", "Magnify the entire screen", {A11Y_APPS: ("screen-magnifier-enabled",)}),
        Group("magnifier", "Magnifier", "Magnification factor, view, screen area and follow behavior",
              {MAGNIFIER: ("mag-factor", "lens-mode", "scroll-at-edges", "screen-position", "mouse-tracking")}),
        Group("crosshairs", "Crosshairs", "Mark the cursor location in the magnifier",
              {MAGNIFIER: ("show-cross-hairs", "cross-hairs-clip", "cross-hairs-thickness",
                           "cross-hairs-length", "cross-hairs-color", "cross-hairs-opacity")}),
        Group("color_filters", "Magnifier color filters", "Invert, brightness, contrast and color",
              {MAGNIFIER: ("invert-lightness", "color-saturation",
                           "brightness-red", "brightness-green", "brightness-blue",
                           "contrast-red", "contrast-green", "contrast-blue")}),
    )
