from .gsettings_module import GSettingsModule


class DockModule(GSettingsModule):
    module_id = "dock"
    display_name = "Pinned apps"
    description = "Apps pinned to the dock and launcher"
    category = "Look and feel"

    # The stock GNOME key; dash-to-dock and the app grid read it too.
    schemas = {"org.gnome.shell": ("favorite-apps",)}
