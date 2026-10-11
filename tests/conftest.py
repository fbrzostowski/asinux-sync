import os
import sys
from pathlib import Path

# Must be set before anything initialises GSettings: tests then use real
# schemas against an in-memory store and never touch the real dconf database.
os.environ["GSETTINGS_BACKEND"] = "memory"
# Schemas installed per-user (e.g. Hanabi) must stay visible although HOME is
# faked per test; they are only read - all values live in the memory backend.
os.environ.setdefault("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from asinux_sync import config as config_module  # noqa: E402
from asinux_sync.modules import background  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config_module, "CONFIG_DIR", home / ".config" / "asinux-sync")
    monkeypatch.setattr(config_module, "CONFIG_PATH", home / ".config" / "asinux-sync" / "config.json")
    monkeypatch.setattr(config_module, "STATE_DIR", home / ".local" / "share" / "asinux-sync")
    monkeypatch.setattr(background, "DAMASK_APP_DIR", home / ".var/app/app.drey.Damask")
    monkeypatch.setattr(background, "DAMASK_KEYFILE", home / ".var/app/app.drey.Damask/config/glib-2.0/settings/keyfile")
    return home


SCHEMAS = (
    "org.gnome.desktop.interface", "org.gnome.desktop.wm.preferences", "org.gnome.mutter", "org.gnome.shell",
    "org.gnome.desktop.background", "org.gnome.desktop.screensaver", "org.gnome.shell.extensions.user-theme",
    "io.github.jeffshee.hanabi-extension", "org.gnome.desktop.wm.keybindings", "org.gnome.shell.keybindings",
    "org.gnome.mutter.keybindings", "org.gnome.mutter.wayland.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys", "org.gnome.settings-daemon.plugins.power", "org.gnome.desktop.session",
)
CUSTOM = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
CUSTOM_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom%d/"


@pytest.fixture(autouse=True)
def clean_gsettings():
    """The memory backend lives as long as the test process: start every test blank."""
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()

    def wipe(schema_id, path=None):
        schema = source.lookup(schema_id, True)
        if schema is None:
            return
        settings = Gio.Settings.new_full(schema, None, path)
        for key in schema.list_keys():
            settings.reset(key)

    for schema_id in SCHEMAS:
        wipe(schema_id)
    for index in range(10):
        wipe(CUSTOM, CUSTOM_PATH % index)
    yield
