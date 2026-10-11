from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import gsettings_util
from .base import Item, SyncModule


@dataclass(frozen=True)
class Group:
    """One switchable item: the GSettings keys it owns, per schema."""

    id: str
    name: str
    description: str = ""
    schemas: dict[str, tuple[str, ...]] = field(default_factory=dict)


class GSettingsModule(SyncModule):
    """A module that is an allowlist of GSettings keys, split into groups."""

    groups: tuple[Group, ...] = ()

    def group_specs(self) -> tuple[Group, ...]:
        return self.groups

    def list_items(self) -> list[Item]:
        return [Item(g.id, g.name, g.description) for g in self.group_specs()]

    def _group(self, item_id: str) -> Group:
        return next(g for g in self.group_specs() if g.id == item_id)

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        values = {}
        for schema_id, keys in self._group(item_id).schemas.items():
            captured = gsettings_util.capture_keys(schema_id, keys)
            if captured is not None:  # schema absent here: say nothing about it
                values[schema_id] = captured
        return {"values": values}

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        values = data.get("values", {})
        for schema_id, keys in self._group(item_id).schemas.items():
            if schema_id in values:
                gsettings_util.apply_keys(schema_id, keys, values[schema_id])
