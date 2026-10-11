from .gsettings_module import Group, GSettingsModule

INTERFACE = "org.gnome.desktop.interface"
WM = "org.gnome.desktop.wm.preferences"


class FontsModule(GSettingsModule):
    module_id = "fonts"
    display_name = "Fonts"
    description = "Interface, document, monospace and window title fonts, hinting and antialiasing"
    category = "Look and feel"

    # The rows of system-configuration's Fonts panel (a port of the GNOME
    # Tweaks tab). The scaling factor is left out on purpose: it is the
    # "Text size" item of Accessibility, and one key shouldn't have two owners.
    groups = (
        Group("interface_font", "Interface text", "", {INTERFACE: ("font-name",)}),
        Group("document_font", "Document text", "", {INTERFACE: ("document-font-name",)}),
        Group("monospace_font", "Monospace text", "", {INTERFACE: ("monospace-font-name",)}),
        Group("titlebar_font", "Window titles", "", {WM: ("titlebar-font", "titlebar-uses-system-font")}),
        Group("hinting", "Hinting", "", {INTERFACE: ("font-hinting",)}),
        Group("antialiasing", "Antialiasing", "Grayscale or subpixel", {INTERFACE: ("font-antialiasing", "font-rgba-order")}),
    )
