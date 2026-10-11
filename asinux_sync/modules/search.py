from .gsettings_module import Group, GSettingsModule

PROVIDERS = "org.gnome.desktop.search-providers"
TRACKER = "org.freedesktop.Tracker3.Miner.Files"


class SearchModule(GSettingsModule):
    module_id = "search"
    display_name = "Search"
    description = "Which apps provide search results, in what order, and where files are searched"
    category = "System"

    # gnome-control-center's Search panel (panels/search): the App Search
    # switch, the per-app list with its order, and Search Locations.
    groups = (
        Group("app_search", "App search", "Include app-provided search results",
              {PROVIDERS: ("disable-external",)}),
        Group("providers", "Search providers", "Which apps may provide results",
              {PROVIDERS: ("enabled", "disabled")}),
        Group("result_order", "Result order", "The order of apps in search results", {PROVIDERS: ("sort-order",)}),
        Group("locations", "Search locations", "Folders searched by system apps",
              {TRACKER: ("index-recursive-directories", "index-single-directories")}),
    )
