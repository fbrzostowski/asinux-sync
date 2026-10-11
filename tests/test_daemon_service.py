import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asinux_sync import config as config_module  # noqa: E402
from asinux_sync import daemon, service  # noqa: E402


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(daemon, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(config_module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(config_module, "MIN_INTERVAL_SECONDS", 1)


def test_tick_does_nothing_without_a_registered_device():
    assert daemon.tick(config_module.Config()) == "not-configured"


def test_daemon_runs_every_interval_and_writes_status(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    config_module.save(config_module.Config(sync_interval_seconds=1))
    ticks = []
    monkeypatch.setattr(daemon, "tick", lambda cfg: ticks.append(time.monotonic()) or "idle")

    stop = threading.Event()
    threading.Timer(2.6, stop.set).start()
    daemon.run(stop)

    assert 2 <= len(ticks) <= 4
    assert json.loads((tmp_path / "status.json").read_text())["result"] == "idle"


def test_daemon_wakes_early_when_config_changes(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(config_module, "MIN_INTERVAL_SECONDS", 100)
    config_module.save(config_module.Config(sync_interval_seconds=100))
    ticks = []
    monkeypatch.setattr(daemon, "tick", lambda cfg: ticks.append(1) or "idle")

    stop = threading.Event()

    def edit_config():
        time.sleep(0.5)
        time.sleep(0.01)
        config_module.save(config_module.Config(sync_interval_seconds=100, disabled_modules=["background"]))

    threading.Thread(target=edit_config).start()
    threading.Timer(1.5, stop.set).start()
    daemon.run(stop)

    assert len(ticks) == 2  # the initial tick plus one triggered by the edit, not 100s later


def test_install_unit_renders_repo_path(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "USER_UNIT_DIR", tmp_path)
    reloads = []
    monkeypatch.setattr(service, "_systemctl", lambda *a, **k: reloads.append(a))

    service.install_unit()
    unit = (tmp_path / service.UNIT_NAME).read_text()
    assert "@REPO@" not in unit and str(service.REPO_DIR) in unit
    assert reloads == [("daemon-reload",)]

    service.install_unit()  # unchanged file: no second reload
    assert reloads == [("daemon-reload",)]
