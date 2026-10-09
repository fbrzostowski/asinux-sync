"""capture() -> apply() must be a no-op for every module.

Uses a fake in-memory Gio.Settings instead of the real dconf database -
these are unit tests, they must never touch the machine's actual desktop
settings (a round-trip here writing to the real session could still flicker
the real wallpaper, and in a headless CI/sandbox there may be no session
bus at all).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asinux_sync.modules.background import BackgroundModule  # noqa: E402


class FakeSettings:
    """Minimal stand-in for Gio.Settings, one instance per schema."""

    _stores: dict[str, dict[str, str]] = {}

    def __init__(self, schema: str):
        self.schema = schema
        self._stores.setdefault(schema, {})

    def get_string(self, key: str) -> str:
        return self._stores[self.schema].get(key, "")

    def set_string(self, key: str, value: str) -> None:
        self._stores[self.schema][key] = value


def test_background_roundtrip_is_noop(tmp_path: Path):
    FakeSettings._stores.clear()
    image = tmp_path / "wallpaper.png"
    image.write_bytes(b"fake-png-bytes")
    FakeSettings._stores["org.gnome.desktop.background"] = {
        "picture-uri": image.as_uri(),
        "picture-uri-dark": image.as_uri(),
        "picture-options": "zoom",
    }
    FakeSettings._stores["org.gnome.desktop.screensaver"] = {"picture-uri": image.as_uri()}

    module = BackgroundModule()
    with mock.patch("asinux_sync.modules.background.Gio.Settings.new", side_effect=FakeSettings), \
         mock.patch("asinux_sync.modules.background.Path.home", return_value=tmp_path / "home"):
        with tempfile.TemporaryDirectory() as assets_dir:
            assets_dir = Path(assets_dir)
            captured = module.capture(assets_dir)
            assert captured["assets"]["background.picture-uri"].endswith("wallpaper.png")

            # Mutate live state to prove apply() actually restores it.
            FakeSettings._stores["org.gnome.desktop.background"]["picture-uri"] = "file:///tmp/other.png"

            module.apply(captured, assets_dir)
            recaptured = module.capture(assets_dir)

            # Applying what we just captured a second time must be a true
            # fixed point: nothing changes anymore.
            module.apply(recaptured, assets_dir)
            twice_captured = module.capture(assets_dir)

    assert recaptured["background"]["picture-options"] == "zoom"
    assert recaptured["background"]["picture-uri"].endswith("wallpaper.png")
    assert twice_captured == recaptured
