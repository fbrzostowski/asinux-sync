"""Two devices fetching a system-wide theme from each other through the
shared request board (device_requests.py + theme_requests.ThemeHandler)."""

from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path

import pytest

from asinux_sync import device_requests, theme_requests
from asinux_sync.modules import appearance
from asinux_sync.modules.appearance import INTERFACE, AppearanceModule


class FakeBackend:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def exists_file(self, path):
        return path in self.files

    def lsjson(self, path):
        prefix = path + "/"
        return [{"Name": p[len(prefix):], "IsDir": False} for p in self.files if p.startswith(prefix)]

    def cat(self, path):
        return self.files[path]

    def rcat(self, path, data):
        self.files[path] = data

    def delete_file(self, path):
        del self.files[path]

    def copyto(self, local, path):
        self.files[path] = Path(local).read_bytes()

    def download(self, path, local):
        Path(local).write_bytes(self.files[path])


class FakeEngine:
    def __init__(self, profile):
        self.last_remote = profile
        self.forgotten = []

    def forget_items(self, keys):
        self.forgotten.extend(keys)


class FakeRegistry:
    def __init__(self, ids):
        self.ids = ids

    def list_devices(self):
        return [{"id": i} for i in self.ids]


class Device:
    """One computer: its own home and /usr/share, its own config."""

    def __init__(self, root: Path, device_id: str, monkeypatch):
        self.root, self.monkeypatch = root, monkeypatch
        self.cfg = type("Cfg", (), {"device_id": device_id, "device_name": device_id.title(), "disabled_modules": []})()

    def icons(self, user=False) -> Path:
        return self.root / ("home/.local/share/icons" if user else "usr/share/icons")

    def activate(self):
        icon_dirs = ((str(self.icons(user=True)), str(self.root / "home/.icons")), (str(self.icons()),))
        theme_dirs = ((str(self.root / "home/.local/share/themes"),), (str(self.root / "usr/share/themes"),))
        self.monkeypatch.setattr(appearance, "THEME_ITEMS", {
            "gtk_theme": (INTERFACE, "gtk-theme", theme_dirs),
            "icon_theme": (INTERFACE, "icon-theme", icon_dirs),
            "cursor_theme": (INTERFACE, "cursor-theme", icon_dirs),
            "shell_theme": (appearance.USER_THEME, "name", theme_dirs),
        })

    def check(self, backend, engine, active=("a", "b")):
        self.activate()
        device_requests.RequestBoard([theme_requests.ThemeHandler()]).check(
            self.cfg, engine, backend, FakeRegistry(list(active)))


BOARD = device_requests.BOARD_PATH


def board(backend) -> dict:
    return json.loads(backend.files[BOARD])["requests"] if BOARD in backend.files else {}


def payloads(backend) -> set[str]:
    return {p for p in backend.files if p.startswith(device_requests.PAYLOAD_DIR + "/")}


def rid(device, item, name):
    return device_requests.request_id(device, "theme", f"{item}/{name}")


def make_theme(folder: Path, inherits: str = "hicolor"):
    (folder / "scalable").mkdir(parents=True)
    (folder / "index.theme").write_text(f"[Icon Theme]\nName={folder.name}\nInherits={inherits}\n")
    (folder / "scalable/a.svg").write_text("<svg/>")
    (folder / "inside.svg").symlink_to("scalable/a.svg")


def profile_with(icon_theme: str) -> dict:
    return {"version": 2, "modules": {"appearance": {"items": {
        "icon_theme": {"values": {INTERFACE: {"icon-theme": f"'{icon_theme}'"}}}}}}}


def test_system_theme_and_what_it_inherits_are_fetched_from_another_device(tmp_path, monkeypatch):
    a, b = Device(tmp_path / "a", "a", monkeypatch), Device(tmp_path / "b", "b", monkeypatch)
    outside = tmp_path / "b/usr/share/pixmaps/logo.svg"
    outside.parent.mkdir(parents=True)
    outside.write_text("<svg id='logo'/>")
    make_theme(b.icons() / "Hatter-Purple", inherits="Hatter,hicolor")
    (b.icons() / "Hatter-Purple/logo.svg").symlink_to(outside)  # absolute, outside the theme
    make_theme(b.icons() / "Hatter")
    make_theme(b.icons() / "hicolor")
    make_theme(a.icons() / "hicolor")

    backend = FakeBackend()
    engine_a, engine_b = FakeEngine(profile_with("Hatter-Purple")), FakeEngine(profile_with("Hatter-Purple"))

    a.check(backend, engine_a)
    purple = rid("a", "icon_theme", "Hatter-Purple")
    assert list(board(backend)) == [purple] and board(backend)[purple]["status"] == "pending"
    assert board(backend)[purple]["data"] == {"item": "icon_theme", "name": "Hatter-Purple"}

    b.check(backend, engine_b)
    assert payloads(backend) == {device_requests.payload_path(purple)}
    assert board(backend)[purple]["status"] == "sent" and board(backend)[purple]["sent_by"] == "b"

    a.check(backend, engine_a)
    got = a.icons(user=True) / "Hatter-Purple"
    assert (got / "index.theme").exists() and (got / appearance.TRANSFER_MARKER).exists()
    assert (got / "inside.svg").is_symlink() and os.readlink(got / "inside.svg") == "scalable/a.svg"
    assert not (got / "logo.svg").is_symlink() and "logo" in (got / "logo.svg").read_text()
    assert "appearance.icon_theme" in engine_a.forgotten
    # Hatter-Purple builds on Hatter, which A lacks too: asked for in the same cycle.
    assert list(board(backend)) == [rid("a", "icon_theme", "Hatter")] and not payloads(backend)

    b.check(backend, engine_b)
    a.check(backend, engine_a)
    assert (a.icons(user=True) / "Hatter/index.theme").exists()
    assert board(backend) == {} and not payloads(backend)

    a.check(backend, engine_a)  # nothing missing any more: nothing asked
    assert board(backend) == {}


def test_a_lost_status_update_does_not_matter(tmp_path, monkeypatch):
    """Both devices write the one file; if B's "sent" is overwritten, A goes by the payload."""
    a, b = Device(tmp_path / "a", "a", monkeypatch), Device(tmp_path / "b", "b", monkeypatch)
    make_theme(b.icons() / "Papirus")
    make_theme(a.icons() / "hicolor")
    backend = FakeBackend()
    engine_a = FakeEngine(profile_with("Papirus"))
    a.check(backend, engine_a)
    stale = backend.files[BOARD]
    b.check(backend, FakeEngine(None))
    backend.files[BOARD] = stale  # A's write raced B's and won
    a.check(backend, engine_a)
    assert (a.icons(user=True) / "Papirus/index.theme").exists()
    assert board(backend) == {} and not payloads(backend)


def test_other_kinds_on_the_board_are_left_alone(tmp_path, monkeypatch):
    a = Device(tmp_path / "a", "a", monkeypatch)
    backend = FakeBackend()
    foreign = {"id": "x", "kind": "something-newer", "requester": "a", "status": "pending", "data": {}}
    backend.files[BOARD] = json.dumps({"version": 1, "requests": {"x": foreign}}).encode()
    a.check(backend, FakeEngine(None))
    assert board(backend) == {"x": foreign}


def test_requests_are_dropped_when_no_longer_needed_or_their_device_is_gone(tmp_path, monkeypatch):
    a, b = Device(tmp_path / "a", "a", monkeypatch), Device(tmp_path / "b", "b", monkeypatch)
    make_theme(b.icons() / "Papirus")
    backend = FakeBackend()

    engine_a = FakeEngine(profile_with("Papirus"))
    a.check(backend, engine_a)
    assert list(board(backend)) == [rid("a", "icon_theme", "Papirus")]
    engine_a.last_remote = profile_with("Other-Missing")
    a.check(backend, engine_a)  # the setting changed: the old request goes
    assert list(board(backend)) == [rid("a", "icon_theme", "Other-Missing")]

    a.cfg.disabled_modules = ["appearance.icon_theme"]
    a.check(backend, engine_a)  # switched off here: nothing to ask
    assert board(backend) == {}

    a.cfg.disabled_modules = []
    engine_a.last_remote = profile_with("Papirus")
    a.check(backend, engine_a)
    b.check(backend, FakeEngine(None), active=("b",))  # A was removed from the account
    assert board(backend) == {} and not payloads(backend)


def test_too_big_theme_is_reported_as_failed(tmp_path, monkeypatch):
    a, b = Device(tmp_path / "a", "a", monkeypatch), Device(tmp_path / "b", "b", monkeypatch)
    make_theme(b.icons() / "Huge")
    monkeypatch.setattr(theme_requests, "MAX_TRANSFER_BYTES", 1)
    backend = FakeBackend()
    a.check(backend, FakeEngine(profile_with("Huge")))
    b.check(backend, FakeEngine(None))
    [request] = board(backend).values()
    assert request["status"] == "failed" and "larger than" in request["reason"]
    a.check(backend, FakeEngine(profile_with("Huge")))  # not asked again over and over
    assert board(backend)[request["id"]]["status"] == "failed"


def test_unpack_takes_only_the_requested_folder(tmp_path):
    source = tmp_path / "src"
    make_theme(source / "Wanted")
    (source / "Other").mkdir()
    archive = tmp_path / "x.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source / "Wanted", "Wanted")
        tar.add(source / "Other", "Other")
    assert theme_requests.unpack(archive, "Wanted", tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["Wanted"]
    assert not theme_requests.unpack(archive, "Missing", tmp_path / "out2")


# -- what the appearance module does meanwhile ------------------------------------------------

def gs(schema):
    from gi.repository import Gio
    return Gio.Settings.new_full(Gio.SettingsSchemaSource.get_default().lookup(schema, True), None, None)


def test_a_missing_theme_is_not_switched_to(tmp_path):
    interface = gs(INTERFACE)
    interface.set_string("icon-theme", "Adwaita")
    AppearanceModule().apply_item(
        "icon_theme", {"values": {INTERFACE: {"icon-theme": "'NoSuchTheme-xyz'"}}}, tmp_path)
    assert interface.get_string("icon-theme") == "Adwaita"


def test_a_fetched_theme_is_not_uploaded_again(tmp_path, isolated_home):
    theme = isolated_home / ".local/share/icons/FetchedIcons"
    make_theme(theme)
    (theme / appearance.TRANSFER_MARKER).write_text("")
    gs(INTERFACE).set_string("icon-theme", "FetchedIcons")
    assert "theme" not in AppearanceModule().capture_item("icon_theme", tmp_path / "assets")
