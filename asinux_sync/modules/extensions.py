from __future__ import annotations

import logging
from pathlib import Path

from . import extension_prefs, shell_extensions
from .base import Item, Mode, ModuleUnavailable, SyncModule

logger = logging.getLogger(__name__)

PREFS = "#prefs"


class ExtensionsModule(SyncModule):
    module_id = "extensions"
    display_name = "Extensions"
    description = "Per extension: sync whether it is on, and optionally its preferences too"
    category = "Look and feel"

    # Each installed extension is two items: its on/off state (`<uuid>`) and
    # its preferences (`<uuid>#prefs`, hidden). The menu in the panel picks
    # one of three combinations of them. Extensions that aren't installed on
    # this machine are not installed for you - they are skipped.
    def list_items(self) -> list[Item]:
        items = []
        names = shell_extensions.installed()
        for uuid, name in sorted(names.items(), key=lambda kv: kv[1].lower()):
            prefs = uuid + PREFS
            items.append(Item(uuid, name, uuid, modes=(
                Mode("Extension and prefs"),
                Mode("Only extension", (prefs,)),
                Mode("Off", (uuid, prefs)),
            )))
            items.append(Item(prefs, f"{name} preferences", hidden=True))
        return items

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        if not item_id.endswith(PREFS):
            return {"enabled": shell_extensions.is_enabled(item_id)}
        try:
            settings = extension_prefs.dump(item_id.removesuffix(PREFS))
        except ModuleUnavailable as exc:  # no dconf: on/off still syncs
            logger.info("extension preferences unavailable here: %s", exc)
            return None
        return {"settings": settings} if settings.strip() else None

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        if not item_id.endswith(PREFS):
            shell_extensions.set_enabled(item_id, bool(data.get("enabled")))
            return
        try:
            extension_prefs.apply(item_id.removesuffix(PREFS), data.get("settings", ""))
        except ModuleUnavailable as exc:
            logger.info("extension preferences unavailable here: %s", exc)
