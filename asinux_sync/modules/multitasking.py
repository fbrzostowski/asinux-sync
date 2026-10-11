from .gsettings_module import Group, GSettingsModule

MUTTER = "org.gnome.mutter"


class MultitaskingModule(GSettingsModule):
    module_id = "multitasking"
    display_name = "Multitasking"
    description = "Hot corner, screen edges, workspaces and app switching"
    category = "Look and feel"

    # One item per row of gnome-control-center's Multitasking panel (checked
    # against its source, panels/multitasking/cc-multitasking-panel.c).
    groups = (
        Group("hot_corner", "Hot corner", "Touch the top-left corner to open the Activities Overview",
              {"org.gnome.desktop.interface": ("enable-hot-corners",)}),
        Group("window_resize", "Window resize", "Drag windows against the top, left and right screen edges to resize them",
              {MUTTER: ("edge-tiling",)}),
        Group("reopen_windows", "Reopen windows after login", "Restore windows that were open when you last used the computer",
              {"org.gnome.desktop.session": ("save-restore",)}),
        Group("workspace_mode", "Dynamic or fixed workspaces", "Automatically remove empty workspaces, or keep a fixed number",
              {MUTTER: ("dynamic-workspaces",)}),
        Group("workspace_count", "Number of workspaces", "How many permanent workspaces there are when they are fixed",
              {"org.gnome.desktop.wm.preferences": ("num-workspaces",)}),
        Group("multi_monitor", "Workspaces on displays", "Workspaces on the primary display only, or on all displays",
              {MUTTER: ("workspaces-only-on-primary",)}),
        Group("app_switching", "App switching", "Include apps from all workspaces, or from the current one only",
              {"org.gnome.shell.app-switcher": ("current-workspace-only",)}),
    )
