from __future__ import annotations

from pathlib import Path

from . import gsettings_util
from .base import SyncModule


class GSettingsModule(SyncModule):
    """A module that is just an allowlist of GSettings keys per schema."""

    schemas: dict[str, tuple[str, ...]] = {}

    def targets(self) -> dict[str, tuple[str, ...]]:
        return self.schemas

    def capture(self, assets_dir: Path) -> dict:
        values = {}
        for schema_id, keys in self.targets().items():
            captured = gsettings_util.capture_keys(schema_id, keys)
            if captured is not None:  # schema absent here: say nothing about it
                values[schema_id] = captured
        return {"values": values}

    def apply(self, data: dict, assets_dir: Path) -> None:
        values = data.get("values", {})
        for schema_id, keys in self.targets().items():
            if schema_id in values:
                gsettings_util.apply_keys(schema_id, keys, values[schema_id])
