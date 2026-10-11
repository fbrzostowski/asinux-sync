"""Base interface every sync module implements.

A module owns one slice of the personalization allowlist (see the module
catalog table in the architecture plan) and is the only place that knows how
to read/write the underlying GSettings keys or config files for that slice.
The engine never touches GSettings/dconf directly - it only calls
capture()/apply() and moves the resulting JSON + any asset files around.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ModuleUnavailable(Exception):
    """This machine can't handle the module (a dependency is missing).
    The engine leaves the module out of capture and apply instead of failing,
    so the cloud's copy of it is kept as is and the rest of the sync goes on."""


class SyncModule(ABC):
    module_id: str
    display_name: str = ""
    description: str = ""
    category: str = "Other"

    @abstractmethod
    def capture(self, assets_dir: Path) -> dict:
        """Read current local state and return JSON-serializable data.

        If the module needs to transfer a binary file (e.g. a wallpaper
        image), it copies that file into `assets_dir` (already created by
        the engine) using a content-addressed name
        (`f"{module_id}-{sha256}-{basename}"`, see
        asinux_sync.engine.asset_name) and references that filename in the
        returned dict. The engine uploads whatever ends up in assets_dir
        alongside profile.json.
        """

    @abstractmethod
    def apply(self, data: dict, assets_dir: Path) -> None:
        """Restore state previously returned by capture().

        `assets_dir` contains any files capture() put there, already
        downloaded locally by the engine. Must be safe to call when `data`
        references an asset that for some reason isn't present (log and
        skip that part, don't raise) - the target machine may be missing
        something the source machine had.
        """
