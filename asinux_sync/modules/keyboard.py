from __future__ import annotations

from pathlib import Path

from . import gsettings_util
from .base import Item
from .gsettings_module import Group, GSettingsModule

INPUT = "org.gnome.desktop.input-sources"
XKB_OPTIONS = "xkb-options"

# The xkb-options list mixes unrelated choices; each item owns the entries
# matching its predicate and leaves all the others as they are.
XKB_ITEMS = {
    "alternate_key": ("Alternate characters key", "The key that types alternate characters (AltGr and similar)",
                      lambda option: option.startswith("lv3:")),
    "compose_key": ("Compose key", "The key for typing special characters in sequences",
                    lambda option: option.startswith("compose:")),
    "layout_options": ("Other layout options", "Caps Lock behaviour, swapped keys and the other xkb options",
                       lambda option: not option.startswith(("lv3:", "compose:"))),
}


class KeyboardModule(GSettingsModule):
    module_id = "keyboard"
    display_name = "Keyboard"
    description = "Input sources, special characters, key repeat, Emacs input"
    category = "Input"

    # The rows of gnome-control-center's Keyboard panel (cc-keyboard-panel.blp)
    # plus the Typing and layout-option rows system-configuration's Keyboard
    # panel adds from GNOME Tweaks. Shortcuts have their own module.
    groups = (
        Group("input_sources", "Input sources", "The keyboard layouts and input methods you use",
              {INPUT: ("sources", "show-all-sources")}),
        Group("per_window", "Switch input sources per window",
              "Remember the input source separately for each window", {INPUT: ("per-window",)}),
        Group("key_repeat", "Key repeat", "Whether keys repeat, after what delay and how fast",
              {"org.gnome.desktop.peripherals.keyboard": ("repeat", "delay", "repeat-interval")}),
        Group("emacs_input", "Emacs input", "Emacs-style key bindings in text fields",
              {"org.gnome.desktop.interface": ("gtk-key-theme",)}),
        Group("overview_key", "Overview key", "Use the Super key to open the Activities Overview",
              {"org.gnome.mutter": ("overlay-key",)}),
    )

    def list_items(self) -> list[Item]:
        by_id = {item.id: item for item in super().list_items()}
        special = [Item(item_id, name, description) for item_id, (name, description, _) in XKB_ITEMS.items()]
        order = [by_id["input_sources"], by_id["per_window"], *special,
                 by_id["key_repeat"], by_id["emacs_input"], by_id["overview_key"]]
        return order

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        if item_id not in XKB_ITEMS:
            return super().capture_item(item_id, assets_dir)
        wanted = XKB_ITEMS[item_id][2]
        return {"options": [o for o in gsettings_util.get_strv(INPUT, XKB_OPTIONS) if wanted(o)]}

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        if item_id not in XKB_ITEMS:
            return super().apply_item(item_id, data, assets_dir)
        owned = XKB_ITEMS[item_id][2]
        kept = [o for o in gsettings_util.get_strv(INPUT, XKB_OPTIONS) if not owned(o)]
        gsettings_util.set_strv(INPUT, XKB_OPTIONS, kept + [o for o in data.get("options", []) if owned(o)])
