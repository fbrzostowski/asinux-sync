from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .base import ModuleUnavailable, SyncModule

logger = logging.getLogger(__name__)

LAYOUTS_PATH = os.environ.get("ASINUX_LAYOUTS_PATH", "/opt/asinux-layouts")


def _extensions():
    """asinux-layouts' extensions module (pure functions, no GTK)."""
    if LAYOUTS_PATH not in sys.path:
        sys.path.append(LAYOUTS_PATH)
    try:
        from asinux_layouts import extensions
    except ImportError as exc:
        raise ModuleUnavailable(f"asinux-layouts not found at {LAYOUTS_PATH}") from exc
    return extensions


class LayoutModule(SyncModule):
    module_id = "layout"
    display_name = "Layout and extensions"
    description = "Enabled GNOME Shell extensions and their settings"
    category = "Look and feel"

    # Delegates entirely to asinux-layouts: capture_current_layout() and
    # apply_layout() already cover which extensions are enabled plus their
    # dconf settings. Extensions that aren't installed on this machine are
    # not installed for you - they are skipped.
    def capture(self, assets_dir: Path) -> dict:
        return {"extensions": _extensions().capture_current_layout()}

    def apply(self, data: dict, assets_dir: Path) -> None:
        _extensions().apply_layout(data.get("extensions", {}))
