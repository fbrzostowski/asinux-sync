"""A system wallpaper one computer lacks, fetched from another through the request board."""

from __future__ import annotations

import json

from gi.repository import Gio

from asinux_sync import device_requests, wallpaper_requests
from asinux_sync.modules import assets, background
from asinux_sync.modules.background import BackgroundModule

from test_theme_requests import FakeBackend, FakeEngine, FakeRegistry


class Cfg:
    def __init__(self, device_id):
        self.device_id, self.device_name, self.disabled_modules = device_id, device_id.title(), []


def check(cfg, backend, engine, home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    device_requests.RequestBoard([wallpaper_requests.WallpaperHandler()]).check(
        cfg, engine, backend, FakeRegistry(["a", "b"]))


def board(backend):
    data = backend.files.get(device_requests.BOARD_PATH)
    return json.loads(data)["requests"] if data else {}


def test_dynamic_system_wallpaper_is_fetched_and_not_pushed_back(tmp_path, monkeypatch):
    system = tmp_path / "sys"  # stands for /usr
    monkeypatch.setattr(assets, "is_system_path", lambda p: str(p).startswith(str(system)))
    xml = system / "backgrounds/Dynamic/Catalina.xml"
    pictures = [system / "backgrounds/Dynamic/Catalina-1.jpg", system / "backgrounds/Dynamic/Catalina-2.jpg"]

    def install_on_b():
        xml.parent.mkdir(parents=True, exist_ok=True)
        for i, picture in enumerate(pictures):
            picture.write_bytes(b"jpeg%d" % i)
        xml.write_text("<background><static><file>%s</file></static>"
                       "<transition><from>%s</from><to>%s</to></transition></background>"
                       % (pictures[0], pictures[0], pictures[1]))

    def uninstall():
        for f in [xml, *pictures]:
            f.unlink()

    home_a, home_b = tmp_path / "home-a", tmp_path / "home-b"
    a, b = Cfg("a"), Cfg("b")
    cloud_item = {"values": {"picture-uri": xml.as_uri(), "picture-uri-dark": xml.as_uri(),
                             "picture-options": "zoom"}, "customised": True, "assets": {}, "bundles": {}}
    profile = {"version": 2, "modules": {"background": {"items": {"desktop": cloud_item}}}}
    backend, engine_a = FakeBackend(), FakeEngine(profile)

    # A lacks the file: it keeps its own wallpaper and asks for it.
    bg = Gio.Settings.new("org.gnome.desktop.background")
    bg.set_string("picture-uri", "file:///mine.png")
    monkeypatch.setenv("HOME", str(home_a))
    BackgroundModule().apply_item("desktop", cloud_item, tmp_path / "assets")
    assert bg.get_string("picture-uri") == "file:///mine.png"
    check(a, backend, engine_a, home_a, monkeypatch)
    assert [r["data"] for r in board(backend).values()] == [{"path": str(xml)}]

    install_on_b()
    check(b, backend, FakeEngine(None), home_b, monkeypatch)
    uninstall()  # back to A's machine, which doesn't have them

    check(a, backend, engine_a, home_a, monkeypatch)
    assert board(backend) == {} and "background.desktop" in engine_a.forgotten
    copy = home_a / ".local/share/asinux-sync/assets/system" / xml.relative_to("/")
    assert copy.is_file()
    for picture in pictures:  # the XML now points at the fetched pictures
        assert str(home_a / ".local/share/asinux-sync/assets/system" / picture.relative_to("/")) in copy.read_text()

    module = BackgroundModule()
    module.apply_item("desktop", cloud_item, tmp_path / "assets")
    assert bg.get_string("picture-uri") == copy.as_uri()
    # Captured as the system path: no difference from the cloud, nothing to push.
    assert module.capture_item("desktop", tmp_path / "assets")["values"] == cloud_item["values"]

    check(a, backend, engine_a, home_a, monkeypatch)  # nothing missing any more
    assert board(backend) == {}


def test_a_device_that_fetched_a_wallpaper_can_pass_it_on(tmp_path, monkeypatch):
    system = tmp_path / "sys"
    monkeypatch.setattr(assets, "is_system_path", lambda p: str(p).startswith(str(system)))
    picture = system / "backgrounds/plain.jpg"
    monkeypatch.setenv("HOME", str(tmp_path / "home-b"))
    copy = background.mirror(picture)
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"jpeg")
    archive = tmp_path / "x.tar.gz"
    assert wallpaper_requests.pack(picture, archive) is None
    monkeypatch.setenv("HOME", str(tmp_path / "home-c"))
    assert wallpaper_requests.unpack(archive, picture)
    assert background.mirror(picture).read_bytes() == b"jpeg"
