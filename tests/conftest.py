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
