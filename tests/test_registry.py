import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asinux_sync import config as config_module  # noqa: E402
from asinux_sync import registry as registry_module  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.files = {}

    def exists_file(self, path):
        return path in self.files

    def exists_dir(self, path):
        return any(p.startswith(path + "/") for p in self.files)

    def cat(self, path):
        return self.files[path]

    def rcat(self, path, data):
        self.files[path] = data

    def lsjson(self, path):
        prefix = path + "/"
        return [{"Name": p[len(prefix):], "IsDir": False} for p in self.files if p.startswith(prefix)]


def test_device_toggles_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    reg = registry_module.Registry(FakeBackend())

    cfg = config_module.Config(device_name="Desktop")
    registry_module.register_this_device(reg, cfg)
    assert cfg.device_id and reg.root_exists()

    # Another device disables wallpaper for this one; next registration adopts it.
    reg.set_device_module(cfg.device_id, "background", False)
    registry_module.register_this_device(reg, cfg)
    assert cfg.disabled_modules == ["background"]

    reg.set_device_module(cfg.device_id, "appearance.icon_theme", False)  # one part of a category
    assert reg.get_device(cfg.device_id)["disabled_modules"] == ["appearance.icon_theme", "background"]

    reg.set_device_module(cfg.device_id, "background", True)
    reg.set_device_module(cfg.device_id, "appearance.icon_theme", True)
    assert reg.get_device(cfg.device_id)["disabled_modules"] == []
    assert [d["name"] for d in reg.list_devices()] == ["Desktop"]
    assert json.loads(reg.backend.files["asinux-sync/devices/%s.json" % cfg.device_id])["name"] == "Desktop"


def test_device_publishes_its_catalog(tmp_path, monkeypatch):
    from asinux_sync.modules.base import Item

    class Mod:
        def list_items(self):
            return [Item("a", "Alpha", "first"), Item("b", "Beta")]

    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    reg = registry_module.Registry(FakeBackend())
    cfg = config_module.Config(device_name="Desktop")
    registry_module.register_this_device(reg, cfg, {"theme": Mod()})
    catalog = reg.get_device(cfg.device_id)["catalog"]
    assert catalog == {"theme": [{"id": "a", "name": "Alpha", "description": "first"},
                                 {"id": "b", "name": "Beta", "description": ""}]}

    registry_module.register_this_device(reg, cfg)  # a heartbeat without modules keeps the last catalog
    assert reg.get_device(cfg.device_id)["catalog"] == catalog


def test_several_switches_can_be_saved_in_one_write(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    reg = registry_module.Registry(FakeBackend())
    cfg = config_module.Config(device_name="Desktop")
    registry_module.register_this_device(reg, cfg)
    reg.set_device_keys(cfg.device_id, {"extensions.a": False, "extensions.a#prefs": False, "extensions.b": True})
    assert reg.get_device(cfg.device_id)["disabled_modules"] == ["extensions.a", "extensions.a#prefs"]


def test_opt_in_modules_start_off_once_per_device(tmp_path, monkeypatch):
    class Mod:
        def __init__(self, default_enabled):
            self.default_enabled = default_enabled

        def list_items(self):
            return []

    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    modules = {"background": Mod(True), "layout": Mod(False), "power": Mod(False)}
    reg = registry_module.Registry(FakeBackend())

    # A device registered before these modules were opt-in picks the default up once...
    cfg = config_module.Config(device_name="Desktop")
    registry_module.register_this_device(reg, cfg)
    assert cfg.disabled_modules == []
    registry_module.register_this_device(reg, cfg, modules)
    assert cfg.disabled_modules == ["layout", "power"]
    assert reg.get_device(cfg.device_id)["defaults_applied"] == ["layout", "power"]

    # ...and turning one on afterwards (from any device) sticks across heartbeats.
    reg.set_device_module(cfg.device_id, "power", True)
    registry_module.register_this_device(reg, cfg, modules)
    registry_module.register_this_device(reg, cfg, modules)
    assert cfg.disabled_modules == ["layout"]

    # A brand-new device starts with them off from its very first registration.
    fresh = config_module.Config(device_name="Laptop")
    registry_module.register_this_device(reg, fresh, modules)
    assert fresh.disabled_modules == ["layout", "power"]
