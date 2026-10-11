"""Every module: capture -> change things -> apply restores, and a second
capture is identical (otherwise the background service would push forever)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from gi.repository import Gio, GLib

from asinux_sync.modules import REGISTRY, background, layout, profile_picture, shortcuts
from asinux_sync.modules.appearance import AppearanceModule
from asinux_sync.modules.background import BackgroundModule
from asinux_sync.modules.base import ModuleUnavailable
from asinux_sync.modules.default_apps import DefaultAppsModule
from asinux_sync.modules.dock import DockModule
from asinux_sync.modules.layout import LayoutModule
from asinux_sync.modules.profile_picture import ProfilePictureModule
from asinux_sync.modules.shortcuts import ShortcutsModule
from asinux_sync.modules.window import WindowModule


@pytest.fixture
def assets_dir(tmp_path):
    path = tmp_path / "assets"
    path.mkdir()
    return path


def gs(schema, path=None):
    return Gio.Settings.new_full(Gio.SettingsSchemaSource.get_default().lookup(schema, True), None, path)


def roundtrip(module, assets_dir, mutate):
    captured = module.capture(assets_dir)
    mutate()
    module.apply(captured, assets_dir)
    restored = module.capture(assets_dir)  # may differ from `captured`: files now live in our own folder
    module.apply(restored, assets_dir)
    assert module.capture(assets_dir) == restored, "not a fixed point - the service would re-push every cycle"
    return captured


def test_every_module_has_ui_metadata():
    for module_id, cls in REGISTRY.items():
        assert cls.module_id == module_id
        assert cls.display_name and cls.description and cls.category


def test_window_roundtrip_and_reset(assets_dir):
    wm = gs("org.gnome.desktop.wm.preferences")
    wm.set_string("button-layout", "close:")
    wm.set_string("focus-mode", "sloppy")

    def mutate():
        wm.set_string("button-layout", ":minimize,maximize,close")
        wm.set_string("action-middle-click-titlebar", "lower")

    roundtrip(WindowModule(), assets_dir, mutate)
    assert wm.get_string("button-layout") == "close:"
    assert wm.get_user_value("action-middle-click-titlebar") is None  # not in profile: reset


def test_apply_skips_schemas_the_profile_does_not_mention(assets_dir):
    wm = gs("org.gnome.desktop.wm.preferences")
    wm.set_string("focus-mode", "mouse")
    WindowModule().apply({"values": {}}, assets_dir)
    assert wm.get_string("focus-mode") == "mouse"


def test_dock_roundtrip(assets_dir):
    shell = gs("org.gnome.shell")
    shell.set_strv("favorite-apps", ["firefox.desktop", "org.gnome.Nautilus.desktop"])
    roundtrip(DockModule(), assets_dir, lambda: shell.set_strv("favorite-apps", ["x.desktop"]))
    assert list(shell.get_strv("favorite-apps")) == ["firefox.desktop", "org.gnome.Nautilus.desktop"]


def test_shortcuts_system_and_custom(assets_dir):
    keys = gs("org.gnome.desktop.wm.keybindings")
    keys.set_strv("close", ["<Super>q"])
    media = shortcuts.MEDIA_KEYS
    base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom%d/"
    gs(media).set_strv("custom-keybindings", [base % 0])
    custom = gs(shortcuts.CUSTOM_SCHEMA, base % 0)
    custom.set_string("name", "Terminal")
    custom.set_string("command", "kgx")
    custom.set_string("binding", "<Super>t")

    def mutate():
        keys.set_strv("close", ["<Alt>F4"])
        gs(media).set_strv("custom-keybindings", [base % 7])
        other = gs(shortcuts.CUSTOM_SCHEMA, base % 7)
        other.set_string("name", "Local only")
        other.set_string("binding", "<Super>l")
        custom.reset("command")

    captured = roundtrip(ShortcutsModule(), assets_dir, mutate)
    assert captured["custom"][0]["values"]["command"] == "'kgx'"
    assert list(keys.get_strv("close")) == ["<Super>q"]
    assert list(gs(media).get_strv("custom-keybindings")) == [base % 0]
    assert custom.get_string("command") == "kgx"
    assert gs(shortcuts.CUSTOM_SCHEMA, base % 7).get_user_value("binding") is None  # stale one cleaned up


def test_default_apps(assets_dir, isolated_home):
    path = isolated_home / ".config" / "mimeapps.list"
    path.parent.mkdir(parents=True)
    path.write_text("[Default Applications]\ntext/html=firefox.desktop\n")
    roundtrip(DefaultAppsModule(), assets_dir, lambda: path.write_text("[Default Applications]\n"))
    assert "firefox.desktop" in path.read_text()


def test_appearance_ships_custom_icon_theme(assets_dir, isolated_home):
    theme = isolated_home / ".local/share/icons/MyIcons"
    (theme / "scalable").mkdir(parents=True)
    (theme / "index.theme").write_text("[Icon Theme]\nName=MyIcons\n")
    (theme / "scalable" / "a.svg").write_text("<svg/>")
    (theme / "link.svg").symlink_to("scalable/a.svg")
    interface = gs("org.gnome.desktop.interface")
    interface.set_string("icon-theme", "MyIcons")
    interface.set_string("color-scheme", "prefer-dark")

    def mutate():
        import shutil
        shutil.rmtree(theme)  # as on a fresh machine
        interface.reset("icon-theme")

    captured = roundtrip(AppearanceModule(), assets_dir, mutate)
    assert captured["themes"]["icons"]["name"] == "MyIcons"
    assert (theme / "index.theme").read_text().startswith("[Icon Theme]")
    assert (theme / "link.svg").is_symlink()
    assert interface.get_string("icon-theme") == "MyIcons"
    assert interface.get_string("color-scheme") == "prefer-dark"


def test_appearance_does_not_ship_system_themes(assets_dir):
    gs("org.gnome.desktop.interface").set_string("gtk-theme", "Adwaita")
    assert AppearanceModule().capture(assets_dir)["themes"] == {}


def test_background_static_image(assets_dir, isolated_home):
    image = isolated_home / "wall.png"
    image.write_bytes(b"png")
    bg = gs(background.BACKGROUND_SCHEMA)
    bg.set_string("picture-uri", image.as_uri())
    bg.set_string("picture-options", "zoom")
    roundtrip(BackgroundModule(), assets_dir, lambda: bg.set_string("picture-uri", "file:///nowhere.png"))
    assert bg.get_string("picture-uri").endswith("wall.png")


def test_background_system_wallpaper_is_referenced_not_uploaded(assets_dir):
    bg = gs(background.BACKGROUND_SCHEMA)
    bg.set_string("picture-uri", "file:///usr/share/backgrounds/does-not-matter.xml")
    data = BackgroundModule().capture(assets_dir)
    assert data["assets"] == {} and data["bundles"] == {}
    assert list(assets_dir.iterdir()) == []


def test_background_user_dynamic_wallpaper_travels_with_its_images(assets_dir, isolated_home):
    folder = isolated_home / ".local/share/backgrounds/Dynamic_Wallpapers"
    folder.mkdir(parents=True)
    (folder / "day.jpg").write_bytes(b"day")
    (folder / "night.jpg").write_bytes(b"night")
    xml = folder / "mine.xml"
    xml.write_text(
        f"<background><static><duration>10</duration><file>{folder}/day.jpg</file></static>"
        f"<transition><duration>1</duration><from>{folder}/day.jpg</from><to>night.jpg</to></transition></background>"
    )
    bg = gs(background.BACKGROUND_SCHEMA)
    bg.set_string("picture-uri", xml.as_uri())

    def mutate():  # a fresh machine: no wallpaper, no files
        import shutil
        shutil.rmtree(folder)
        bg.reset("picture-uri")

    captured = roundtrip(BackgroundModule(), assets_dir, mutate)
    restored = Path(bg.get_string("picture-uri").removeprefix("file://"))
    text = restored.read_text()
    assert "@BUNDLE@" not in text
    for image in ("day.jpg", "night.jpg"):
        assert any(Path(p).is_file() for p in [str(restored.parent / "images" / n) for n in
                                                [x.name for x in (restored.parent / "images").iterdir()] if n.endswith(image)])
    assert len(captured["bundles"]) == 1


def test_background_hanabi_video_and_local_only_keys(assets_dir, isolated_home):
    if background.gsettings_util.schema(background.HANABI_SCHEMA) is None:
        pytest.skip("Hanabi schema not installed")
    video = isolated_home / "loop.mp4"
    video.write_bytes(b"video")
    hanabi = gs(background.HANABI_SCHEMA)
    hanabi.set_string("video-path", str(video))
    hanabi.set_boolean("mute", True)
    hanabi.set_boolean("enable-va", True)  # driver specific - must stay local

    def mutate():
        hanabi.set_string("video-path", "/gone.mp4")
        hanabi.set_boolean("mute", False)
        hanabi.set_boolean("enable-va", False)

    captured = roundtrip(BackgroundModule(), assets_dir, mutate)
    assert "enable-va" not in captured["hanabi"]["values"]
    assert hanabi.get_boolean("mute") is True
    assert hanabi.get_boolean("enable-va") is False
    assert Path(hanabi.get_string("video-path")).read_bytes() == b"video"


def test_background_damask_only_where_installed(assets_dir, isolated_home):
    keyfile = background.DAMASK_KEYFILE
    keyfile.parent.mkdir(parents=True)
    keyfile.write_text("[app/drey/Damask]\nactive-source='bing'\n")
    module = BackgroundModule()
    captured = module.capture(assets_dir)
    assert "bing" in captured["damask"]
    keyfile.write_text("changed")
    module.apply(captured, assets_dir)
    assert "bing" in keyfile.read_text()

    import shutil
    shutil.rmtree(background.DAMASK_APP_DIR)
    module.apply(captured, assets_dir)  # not installed: nothing is created
    assert not background.DAMASK_APP_DIR.exists()


def test_layout_delegates_to_asinux_layouts(assets_dir, monkeypatch):
    applied = []
    fake = SimpleNamespace(
        capture_current_layout=lambda: {"dash@x": {"enabled": True, "settings": "[/]\na=1\n"}},
        apply_layout=applied.append,
    )
    monkeypatch.setattr(layout, "_extensions", lambda: fake)
    module = LayoutModule()
    data = module.capture(assets_dir)
    module.apply(data, assets_dir)
    assert applied == [data["extensions"]]


def test_layout_unavailable_without_asinux_layouts(assets_dir, monkeypatch):
    monkeypatch.setattr(layout, "LAYOUTS_PATH", "/nonexistent")
    monkeypatch.delitem(__import__("sys").modules, "asinux_layouts", raising=False)
    monkeypatch.setattr("sys.path", [p for p in __import__("sys").path if "asinux-layouts" not in p])
    with pytest.raises(ModuleUnavailable):
        LayoutModule().capture(assets_dir)


class FakeProxy:
    def __init__(self, icon):
        self.icon = icon
        self.calls = []

    def get_cached_property(self, name):
        return GLib.Variant("s", str(self.icon)) if self.icon else None

    def call_sync(self, method, args, *_):
        self.calls.append((method, args.unpack()[0]))
        self.icon = Path(args.unpack()[0])


def test_profile_picture(assets_dir, isolated_home, monkeypatch):
    mine = isolated_home / "me.png"
    mine.write_bytes(b"face")
    proxy = FakeProxy(mine)
    monkeypatch.setattr(profile_picture, "_user_proxy", lambda: proxy)
    module = ProfilePictureModule()
    captured = module.capture(assets_dir)

    module.apply(captured, assets_dir)
    assert proxy.calls == []  # same picture already set: nothing to do

    proxy.icon = isolated_home / "other.png"
    proxy.icon.write_bytes(b"other")
    module.apply(captured, assets_dir)
    assert proxy.calls and Path(proxy.calls[0][1]).read_bytes() == b"face"
    assert module.capture(assets_dir) == captured


def test_profile_picture_without_accountsservice(assets_dir, monkeypatch):
    def unavailable():
        raise ModuleUnavailable("no bus")

    monkeypatch.setattr(profile_picture, "_user_proxy", unavailable)
    with pytest.raises(ModuleUnavailable):
        ProfilePictureModule().capture(assets_dir)
