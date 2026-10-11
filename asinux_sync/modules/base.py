"""Base interface every sync module implements.

A module owns one slice of the personalization allowlist and is the only
place that knows how to read/write the underlying GSettings keys or files for
that slice. The engine never touches GSettings/dconf directly - it only calls
capture()/apply() and moves the resulting JSON + any asset files around.

A module is split into *items*: the smallest things a device can switch off
on its own (one theme kind, one extension, ...). Items are also the unit of
the cloud profile (`{"items": {item_id: data}}`), so a device that switches
an item off keeps the cloud's copy of it intact for the other devices.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

ALL = "all"


class ModuleUnavailable(Exception):
    """This machine can't handle the module (a dependency is missing).
    The engine leaves the module out of capture and apply instead of failing,
    so the cloud's copy of it is kept as is and the rest of the sync goes on."""


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    description: str = ""


class SyncModule(ABC):
    module_id: str
    display_name: str = ""
    description: str = ""
    category: str = "Other"

    def list_items(self) -> list[Item]:
        """What can be switched off separately. May depend on the machine
        (e.g. one item per installed extension). A module with a single item
        is shown as a plain switch."""
        return [Item(ALL, self.display_name, self.description)]

    @abstractmethod
    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        """Current local state of one item as JSON-serializable data, or None
        if there is nothing to sync for it here. Files to transfer are copied
        into `assets_dir` (see assets.py)."""

    @abstractmethod
    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        """Restore one item from data returned by capture_item(). Must cope
        with a missing asset (log and skip) - the other machine may have had
        something this one doesn't."""

    def merge_item(self, item_id: str, remote: dict, local: dict) -> dict:
        """What to upload for an item both the cloud and this device have.
        Normally this device's data replaces the cloud's; a module whose item
        is a collection (e.g. settings of many extensions) can keep the
        entries this device doesn't know about."""
        return local

    def capture(self, assets_dir: Path, disabled_items: frozenset[str] | set[str] = frozenset()) -> dict:
        items = {}
        for item in self.list_items():
            if item.id in disabled_items:
                continue
            data = self.capture_item(item.id, assets_dir)
            if data is not None:
                items[item.id] = data
        return {"items": items}

    def apply(self, data: dict, assets_dir: Path, disabled_items: frozenset[str] | set[str] = frozenset()) -> None:
        stored = data.get("items", {})
        for item in self.list_items():
            if item.id in disabled_items or item.id not in stored:
                continue
            try:
                self.apply_item(item.id, stored[item.id], assets_dir)
            except ModuleUnavailable:
                raise
            except Exception:
                logger.exception("applying %s.%s failed, skipping it", self.module_id, item.id)
