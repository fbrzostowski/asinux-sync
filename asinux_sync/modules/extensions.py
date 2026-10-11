from __future__ import annotations

from pathlib import Path

from . import shell_extensions
from .base import Item, SyncModule


class ExtensionsModule(SyncModule):
    module_id = "extensions"
    display_name = "Extensions"
    description = "Which GNOME Shell extensions are turned on - one switch per installed extension"
    category = "Look and feel"

    # Only on/off is synced here; how an extension is configured belongs to
    # the Layout module. Extensions that aren't installed on this machine are
    # not installed for you - they are skipped.
    def list_items(self) -> list[Item]:
        names = shell_extensions.installed()
        return [Item(uuid, name, uuid) for uuid, name in sorted(names.items(), key=lambda kv: kv[1].lower())]

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        return {"enabled": shell_extensions.is_enabled(item_id)}

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        shell_extensions.set_enabled(item_id, bool(data.get("enabled")))
