from .gsettings_module import Group, GSettingsModule

COLOR = "org.gnome.settings-daemon.plugins.color"


class NightLightModule(GSettingsModule):
    module_id = "night_light"
    display_name = "Night Light"
    description = "Warmer screen colors in the evening"
    category = "Look and feel"

    # The Night Light part of gnome-control-center's Displays panel. The
    # display layout itself belongs to the machine and isn't synced, and
    # night-light-last-coordinates is location state, not a preference.
    groups = (
        Group("enabled", "Night Light", "Turn Night Light on or off", {COLOR: ("night-light-enabled",)}),
        Group("schedule", "Schedule", "Sunset to sunrise, or fixed hours",
              {COLOR: ("night-light-schedule-automatic", "night-light-schedule-from", "night-light-schedule-to")}),
        Group("temperature", "Color temperature", "How warm the screen gets", {COLOR: ("night-light-temperature",)}),
    )
