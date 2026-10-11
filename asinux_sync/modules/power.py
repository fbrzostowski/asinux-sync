from .gsettings_module import Group, GSettingsModule

POWER = "org.gnome.settings-daemon.plugins.power"


class PowerModule(GSettingsModule):
    module_id = "power"
    display_name = "Power"
    description = "Power button, screen dimming and blanking, automatic suspend"
    category = "System"
    default_enabled = False

    # The rows of gnome-control-center's Power panel, one item each. Laptop
    # and desktop usually want different values: switch off what shouldn't
    # travel between them.
    groups = (
        Group("power_button", "Power button behavior", "What pressing the power button does",
              {POWER: ("power-button-action",)}),
        Group("battery_percentage", "Show battery percentage", "Exact charge level in the top bar",
              {"org.gnome.desktop.interface": ("show-battery-percentage",)}),
        Group("dim_screen", "Dim screen", "Reduce brightness when the device is inactive",
              {POWER: ("idle-dim", "idle-brightness")}),
        Group("auto_brightness", "Automatic brightness", "Adjust brightness to the ambient light",
              {POWER: ("ambient-enabled",)}),
        Group("auto_power_saver", "Automatic power saver", "Turn on power saver when the battery is low",
              {POWER: ("power-saver-profile-on-low-battery",)}),
        Group("screen_blank", "Automatic screen blank", "Turn the screen off after a period of inactivity",
              {"org.gnome.desktop.session": ("idle-delay",)}),
        Group("suspend_battery", "Automatic suspend on battery", "Whether and when the device suspends on battery power",
              {POWER: ("sleep-inactive-battery-type", "sleep-inactive-battery-timeout")}),
        Group("suspend_plugged", "Automatic suspend when plugged in", "Whether and when the device suspends on mains power",
              {POWER: ("sleep-inactive-ac-type", "sleep-inactive-ac-timeout")}),
    )
