import json
from types import SimpleNamespace

import pytest
from gi.repository import Gio, GLib

from asinux_sync import config as config_module
from asinux_sync import daemon, missing_extensions
from asinux_sync import registry as registry_module
from asinux_sync.engine import SyncEngine
from asinux_sync.modules import shell_extensions
from asinux_sync.modules.extensions import ExtensionsModule

PROFILE = {"version": 2, "modules": {"extensions": {"items": {
    "a@x": {"enabled": True}, "a@x#prefs": {"settings": {}},
    "b@x": {"enabled": False}, "c@x": {"enabled": True}, "d@x": {"enabled": True}}}}}


def test_only_enabled_missing_undeclined_extensions_are_asked_about():
    found = missing_extensions.find_missing(PROFILE, disabled=[], declined=["d@x"], installed=["c@x"])
    assert found == ["a@x"]  # b is off in the cloud, c is installed, d was declined, #prefs is not an extension


def test_nothing_is_asked_when_the_device_does_not_sync_extensions():
    assert missing_extensions.find_missing(PROFILE, ["extensions"], [], []) == []
    assert missing_extensions.find_missing(PROFILE, ["extensions.a@x"], [], ["c@x", "d@x"]) == []
    assert missing_extensions.find_missing(None, [], [], []) == []
    assert missing_extensions.find_missing({"version": 1, "modules": {}}, [], [], []) == []


def test_names_come_from_the_catalogs_other_devices_publish():
    devices = [{"catalog": {"extensions": [{"id": "a@x", "name": "Alpha"}, {"id": "a@x#prefs", "name": "Alpha preferences", "hidden": True}]}},
               {"catalog": {"extensions": [{"id": "b@x", "name": "Beta"}]}}, {}]
    assert missing_extensions.names_from_devices(devices) == {"a@x": "Alpha", "b@x": "Beta"}


def test_build_result_sorts_out_every_outcome():
    result = missing_extensions.build_result(
        ["a", "b", "c", "d", "e"], {"a", "b", "c", "d"},
        {"a": "successful", "b": "already-installed", "c": "cancelled", "d": "error: not found"})
    assert result == {"installed": ["a", "b"], "declined": ["c", "d", "e"], "failed": {"d": "error: not found"}}


class FakeShell:
    def __init__(self, answer):
        self.answer = answer

    def call_sync(self, method, args, *_):
        assert method == "InstallRemoteExtension"
        if isinstance(self.answer, Exception):
            raise self.answer
        return GLib.Variant("(s)", (self.answer,))


def test_install_goes_through_gnome_shell(monkeypatch):
    monkeypatch.setattr(Gio.DBusProxy, "new_for_bus_sync", lambda *a: FakeShell("successful"))
    assert missing_extensions.install_remote("a@x") == "successful"
    monkeypatch.setattr(Gio.DBusProxy, "new_for_bus_sync", lambda *a: FakeShell("cancelled"))
    assert missing_extensions.install_remote("a@x") == "cancelled"
    boom = GLib.Error.new_literal(GLib.quark_from_string("x"), "no shell", 0)
    monkeypatch.setattr(Gio.DBusProxy, "new_for_bus_sync", lambda *a: FakeShell(boom))
    assert missing_extensions.install_remote("a@x").startswith("error: no shell")


# -- the service's side: asking once, remembering the answer ------------------------------------------------

class FakeProcess:
    def __init__(self):
        self.running = True
        self.returncode = 0

    def poll(self):
        return None if self.running else self.returncode


class Harness:
    def __init__(self, monkeypatch, profile=PROFILE, installed=()):
        self.launched = []
        self.now = 1000.0
        self.forgotten = []
        self.engine = SimpleNamespace(last_remote=profile, forget_items=self.forgotten.extend)
        self.cfg = config_module.Config(device_id="me")
        monkeypatch.setattr(registry_module.Registry, "list_devices",
                            lambda self: [{"catalog": {"extensions": [{"id": "a@x", "name": "Alpha"}]}}])
        self.prompter = daemon.ExtensionPrompter(launch=self.launch, installed=lambda: dict.fromkeys(installed, ""), clock=lambda: self.now)

    def launch(self, request, result):
        self.request = json.loads(request.read_text())
        self.result_path = result
        self.process = FakeProcess()
        self.launched.append(self.request)
        return self.process

    def check(self):
        self.prompter.check(self.cfg, self.engine, backend=None)

    def answer(self, **result):
        self.result_path.write_text(json.dumps({"installed": [], "declined": [], "failed": {}, **result}))
        self.process.running = False


def test_the_user_is_asked_once_with_names(monkeypatch):
    h = Harness(monkeypatch, installed=["c@x"])
    h.check()
    h.check()  # the window is still open: no second one
    assert len(h.launched) == 1
    assert h.request["extensions"] == [{"uuid": "a@x", "name": "Alpha"}, {"uuid": "d@x", "name": "d@x"}]


def test_nothing_missing_means_no_window(monkeypatch):
    h = Harness(monkeypatch, installed=["a@x", "c@x", "d@x"])
    h.check()
    assert h.launched == []


def test_no_thanks_is_remembered_and_never_asked_again(monkeypatch):
    h = Harness(monkeypatch)
    h.check()
    h.answer(declined=["a@x", "c@x", "d@x"])
    h.check()
    assert h.cfg.declined_extensions == ["a@x", "c@x", "d@x"]
    assert config_module.load().declined_extensions == ["a@x", "c@x", "d@x"]  # saved: survives a restart
    h.now += 10**6
    h.check()
    assert len(h.launched) == 1


def test_installed_extensions_get_their_cloud_settings_applied_next_cycle(monkeypatch):
    h = Harness(monkeypatch)
    h.check()
    h.answer(installed=["a@x"], declined=["c@x", "d@x"])
    h.check()
    assert set(h.forgotten) == {"extensions.a@x", "extensions.a@x#prefs", "layout.all", "appearance.shell_theme"}


def test_closing_the_window_asks_again_but_not_at_once(monkeypatch):
    h = Harness(monkeypatch)
    h.check()
    h.process.running = False  # closed without choosing: no result file
    h.check()
    assert h.cfg.declined_extensions == []  # nothing was decided
    h.now += 60
    h.check()
    assert len(h.launched) == 1  # still cooling down
    h.now += daemon.PROMPT_COOLDOWN_SECONDS
    h.check()
    assert len(h.launched) == 2


def test_declining_one_extension_still_asks_about_the_others(monkeypatch):
    h = Harness(monkeypatch)
    h.check()
    assert [e["uuid"] for e in h.launched[0]["extensions"]] == ["a@x", "c@x", "d@x"]
    h.answer(declined=["a@x"])
    h.check()  # the answer is recorded, and what is still missing is asked about
    assert h.cfg.declined_extensions == ["a@x"]
    assert [e["uuid"] for e in h.launched[1]["extensions"]] == ["c@x", "d@x"]


def test_reset_command_forgets_the_declined_ones(capsys):
    from asinux_sync import cli

    cfg = config_module.Config(declined_extensions=["a@x", "b@x"])
    config_module.save(cfg)
    assert cli.main(["reset-extension-prompts"]) == 0
    assert config_module.load().declined_extensions == []
    assert "a@x, b@x" in capsys.readouterr().out


# -- the case that matters most: an extension installed later keeps its cloud settings ---------------------

def gs_shell():
    return Gio.Settings.new_full(Gio.SettingsSchemaSource.get_default().lookup("org.gnome.shell", True), None, None)


class DirBackend:
    def __init__(self, root):
        self.root = root

    def exists_dir(self, path):
        return (self.root / path).is_dir()

    def sync(self, local_dir, remote_path, *, direction="push"):
        import shutil
        from pathlib import Path
        src, dst = (Path(local_dir), self.root / remote_path) if direction == "push" else (self.root / remote_path, Path(local_dir))
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def test_after_installing_a_missing_extension_the_cloud_value_wins(tmp_path, monkeypatch):
    shell = gs_shell()
    cloud = tmp_path / "cloud"
    monkeypatch.setattr(shell_extensions, "installed", lambda: {"x@y": "X"})
    a = SyncEngine(DirBackend(cloud), {"extensions": ExtensionsModule()}, tmp_path / "a")
    shell.set_strv("enabled-extensions", ["x@y"])
    a.sync_once()  # the other computer has X turned on

    b = SyncEngine(DirBackend(cloud), {"extensions": ExtensionsModule()}, tmp_path / "b")
    shell.set_strv("enabled-extensions", [])
    monkeypatch.setattr(shell_extensions, "installed", lambda: {})  # B doesn't have X
    b.sync_once()
    assert list(shell.get_strv("enabled-extensions")) == []  # nothing to turn on yet

    monkeypatch.setattr(shell_extensions, "installed", lambda: {"x@y": "X"})  # the user installs it
    b.forget_items(["extensions.x@y", "extensions.x@y#prefs", "layout.all"])
    assert b.sync_once() == "pulled"
    assert list(shell.get_strv("enabled-extensions")) == ["x@y"]  # the cloud's "on" was applied
    cloud_profile = json.loads((cloud / "asinux-sync/profile/profile.json").read_text())
    assert cloud_profile["modules"]["extensions"]["items"]["x@y"] == {"enabled": True}


def test_without_forgetting_the_new_extension_would_overwrite_the_cloud(tmp_path, monkeypatch):
    """Why forget_items exists: otherwise 'installed but off' looks like a local change."""
    shell = gs_shell()
    cloud = tmp_path / "cloud"
    monkeypatch.setattr(shell_extensions, "installed", lambda: {"x@y": "X"})
    a = SyncEngine(DirBackend(cloud), {"extensions": ExtensionsModule()}, tmp_path / "a")
    shell.set_strv("enabled-extensions", ["x@y"])
    a.sync_once()
    b = SyncEngine(DirBackend(cloud), {"extensions": ExtensionsModule()}, tmp_path / "b")
    shell.set_strv("enabled-extensions", [])
    monkeypatch.setattr(shell_extensions, "installed", lambda: {})
    b.sync_once()
    monkeypatch.setattr(shell_extensions, "installed", lambda: {"x@y": "X"})
    assert b.sync_once() == "pushed"  # B would upload "x@y is off" - the bug forget_items prevents
