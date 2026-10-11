from .gsettings_module import Group, GSettingsModule

INTERFACE = "org.gnome.desktop.interface"
CALENDAR = "org.gnome.desktop.calendar"


class DateTimeModule(GSettingsModule):
    module_id = "datetime"
    display_name = "Date and time"
    description = "Time format, week start and what the clock shows. Not the date, network time or time zone"
    category = "System"

    # The user-level rows of gnome-control-center's Date & Time page
    # (panels/system/datetime/cc-datetime-page.c). Deliberately not synced:
    # Automatic Date & Time, Date & Time and Time Zone are system-wide
    # (timedated), need an administrator, and are tied to where the machine is.
    groups = (
        Group("auto_timezone", "Automatic time zone", "Use location services to set the time zone",
              {"org.gnome.desktop.datetime": ("automatic-timezone",)}),
        Group("time_format", "Time format", "24-hour or AM/PM",
              {INTERFACE: ("clock-format",), "org.gtk.Settings.FileChooser": ("clock-format",)}),
        Group("first_weekday", "First day of the week", "", {CALENDAR: ("week-start-day",)}),
        Group("clock_weekday", "Clock: week day", "Show the week day in the top bar", {INTERFACE: ("clock-show-weekday",)}),
        Group("clock_date", "Clock: date", "Show the date in the top bar", {INTERFACE: ("clock-show-date",)}),
        Group("clock_seconds", "Clock: seconds", "Show seconds in the top bar", {INTERFACE: ("clock-show-seconds",)}),
        Group("week_numbers", "Week numbers", "Shown in the dropdown calendar", {CALENDAR: ("show-weekdate",)}),
    )
