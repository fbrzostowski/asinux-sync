"""Every module: capture -> change things -> apply restores, and a second
capture is identical (otherwise the background service would push forever)."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from gi.repository import Gio, GLib

from asinux_sync import registry as registry_module
from asinux_sync.modules import REGISTRY, background, extension_prefs, profile_picture, shell_extensions, shortcuts
from asinux_sync.modules.appearance import AppearanceModule
from asinux_sync.modules.background import BackgroundModule
from asinux_sync.modules.base import ModuleUnavailable
from asinux_sync.modules.default_apps import DefaultAppsModule
from asinux_sync.modules.dock import DockModule
from asinux_sync.modules.extensions import ExtensionsModule
from asinux_sync.modules.layout import LayoutModule
from asinux_sync.modules.power import PowerModule
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


def test_every_module_has_ui_metadata_and_unique_items():
    for module_id, cls in REGISTRY.items():
        assert cls.module_id == module_id
        assert cls.display_name and cls.description and cls.category
        ids = [item.id for item in cls().list_items()]
        assert len(ids) == len(set(ids)), module_id


def test_extensions_and_layout_are_separate_modules():
    assert "extensions" in REGISTRY and "layout" in REGISTRY
    assert LayoutModule.module_id != ExtensionsModule.module_id


def test_catalog_lists_every_module(monkeypatch):
    monkeypatch.setattr(shell_extensions, "installed", lambda: {"a@x": "A"})
    catalog = registry_module.build_catalog({mid: cls() for mid, cls in REGISTRY.items()})
    assert set(catalog) == set(REGISTRY)
    assert [i["id"] for i in catalog["extensions"]] == ["a@x", "a@x#prefs"]
    assert {i["id"] for i in catalog["appearance"]} >= {"icon_theme", "cursor_theme", "cursor_size"}


# -- window -----------------------------------------------------------------

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


def test_window_items_are_independent(assets_dir):
    module = WindowModule()
    assert {i.id for i in module.list_items()} == {
        "titlebar_buttons", "titlebar_actions", "focus", "mouse_modifier", "placement"}
    wm = gs("org.gnome.desktop.wm.preferences")
    wm.set_string("button-layout", "close:")
    wm.set_string("focus-mode", "sloppy")
    captured = module.capture(assets_dir)

    wm.set_string("button-layout", "appmenu:close")
    wm.set_string("focus-mode", "click")
    module.apply(captured, assets_dir, disabled_items={"focus"})
    assert wm.get_string("button-layout") == "close:"  # applied
    assert wm.get_string("focus-mode") == "click"  # this item is switched off here

    only_buttons = module.capture(assets_dir, disabled_items={"focus", "titlebar_actions", "mouse_modifier", "placement"})
    assert set(only_buttons["items"]) == {"titlebar_buttons"}


def test_apply_skips_schemas_the_profile_does_not_mention(assets_dir):
    wm = gs("org.gnome.desktop.wm.preferences")
    wm.set_string("focus-mode", "mouse")
    WindowModule().apply({"items": {"focus": {"values": {}}}}, assets_dir)
    assert wm.get_string("focus-mode") == "mouse"


def test_dock_roundtrip(assets_dir):
    shell = gs("org.gnome.shell")
    shell.set_strv("favorite-apps", ["firefox.desktop", "org.gnome.Nautilus.desktop"])
    roundtrip(DockModule(), assets_dir, lambda: shell.set_strv("favorite-apps", ["x.desktop"]))
    assert list(shell.get_strv("favorite-apps")) == ["firefox.desktop", "org.gnome.Nautilus.desktop"]


# -- shortcuts --------------------------------------------------------------------

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
    assert captured["items"]["custom"]["custom"][0]["values"]["command"] == "'kgx'"
    assert list(keys.get_strv("close")) == ["<Super>q"]
    assert list(gs(media).get_strv("custom-keybindings")) == [base % 0]
    assert custom.get_string("command") == "kgx"
    assert gs(shortcuts.CUSTOM_SCHEMA, base % 7).get_user_value("binding") is None  # stale one cleaned up


def test_shortcut_groups_can_be_switched_off_separately(assets_dir):
    module = ShortcutsModule()
    assert [i.id for i in module.list_items()] == ["windows", "shell", "mutter", "media_keys", "custom"]
    keys = gs("org.gnome.desktop.wm.keybindings")
    keys.set_strv("close", ["<Super>q"])
    captured = module.capture(assets_dir)
    keys.set_strv("close", ["<Alt>F4"])
    module.apply(captured, assets_dir, disabled_items={"windows"})
    assert list(keys.get_strv("close")) == ["<Alt>F4"]


def test_default_apps(assets_dir, isolated_home):
    path = isolated_home / ".config" / "mimeapps.list"
    path.parent.mkdir(parents=True)
    path.write_text("[Default Applications]\ntext/html=firefox.desktop\n")
    roundtrip(DefaultAppsModule(), assets_dir, lambda: path.write_text("[Default Applications]\n"))
    assert "firefox.desktop" in path.read_text()


# -- appearance ----------------------------------------------------------------------------

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
        shutil.rmtree(theme)  # as on a fresh machine
        interface.reset("icon-theme")

    captured = roundtrip(AppearanceModule(), assets_dir, mutate)
    assert captured["items"]["icon_theme"]["theme"]["name"] == "MyIcons"
    assert (theme / "index.theme").read_text().startswith("[Icon Theme]")
    assert (theme / "link.svg").is_symlink()
    assert interface.get_string("icon-theme") == "MyIcons"
    assert interface.get_string("color-scheme") == "prefer-dark"


def test_appearance_parts_are_independent(assets_dir):
    module = AppearanceModule()
    assert [i.id for i in module.list_items()] == [
        "gtk_theme", "icon_theme", "cursor_theme", "cursor_size", "color_scheme", "accent_color", "shell_theme"]
    interface = gs("org.gnome.desktop.interface")
    interface.set_string("icon-theme", "Papirus")
    interface.set_int("cursor-size", 48)
    interface.set_string("color-scheme", "prefer-dark")
    captured = module.capture(assets_dir)

    interface.set_string("icon-theme", "Adwaita")
    interface.set_int("cursor-size", 24)
    interface.set_string("color-scheme", "default")
    module.apply(captured, assets_dir, disabled_items={"icon_theme", "cursor_size"})
    assert interface.get_string("icon-theme") == "Adwaita"
    assert interface.get_int("cursor-size") == 24
    assert interface.get_string("color-scheme") == "prefer-dark"

    without = module.capture(assets_dir, disabled_items={"cursor_size"})
    assert "cursor_size" not in without["items"] and "icon_theme" in without["items"]


def test_appearance_does_not_ship_system_themes(assets_dir):
    gs("org.gnome.desktop.interface").set_string("gtk-theme", "Adwaita")
    assert "theme" not in AppearanceModule().capture(assets_dir)["items"]["gtk_theme"]


# -- wallpaper -------------------------------------------------------------------------------

def test_background_static_image(assets_dir, isolated_home):
    image = isolated_home / "wall.png"
    image.write_bytes(b"png")
    bg = gs(background.BACKGROUND_SCHEMA)
    bg.set_string("picture-uri", image.as_uri())
    bg.set_string("picture-options", "zoom")
    roundtrip(BackgroundModule(), assets_dir, lambda: bg.set_string("picture-uri", "file:///nowhere.png"))
    assert bg.get_string("picture-uri").endswith("wall.png")
    assert bg.get_string("picture-options") == "zoom"


def test_background_items_are_independent(assets_dir, isolated_home):
    image = isolated_home / "wall.png"
    image.write_bytes(b"png")
    lock = isolated_home / "lock.png"
    lock.write_bytes(b"lock")
    bg, ss = gs(background.BACKGROUND_SCHEMA), gs(background.SCREENSAVER_SCHEMA)
    bg.set_string("picture-uri", image.as_uri())
    ss.set_string("picture-uri", lock.as_uri())
    module = BackgroundModule()
    captured = module.capture(assets_dir)
    bg.set_string("picture-uri", "file:///usr/share/other.jpg")
    ss.set_string("picture-uri", "file:///usr/share/other2.jpg")
    module.apply(captured, assets_dir, disabled_items={"desktop"})
    assert bg.get_string("picture-uri") == "file:///usr/share/other.jpg"  # desktop switched off
    assert ss.get_string("picture-uri").endswith("lock.png")


def test_background_system_wallpaper_is_referenced_not_uploaded(assets_dir):
    bg = gs(background.BACKGROUND_SCHEMA)
    bg.set_string("picture-uri", "file:///usr/share/backgrounds/does-not-matter.xml")
    data = BackgroundModule().capture(assets_dir)["items"]["desktop"]
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
        shutil.rmtree(folder)
        bg.reset("picture-uri")

    captured = roundtrip(BackgroundModule(), assets_dir, mutate)
    restored = Path(bg.get_string("picture-uri").removeprefix("file://"))
    assert "@BUNDLE@" not in restored.read_text()
    images = sorted(p.name for p in (restored.parent / "images").iterdir())
    assert any(n.endswith("day.jpg") for n in images) and any(n.endswith("night.jpg") for n in images)
    assert len(captured["items"]["desktop"]["bundles"]) == 1


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
    assert "enable-va" not in captured["items"]["live_wallpaper"]["values"]
    assert hanabi.get_boolean("mute") is True
    assert hanabi.get_boolean("enable-va") is False
    assert Path(hanabi.get_string("video-path")).read_bytes() == b"video"


def test_background_damask_only_where_installed(assets_dir, isolated_home):
    keyfile = background.DAMASK_KEYFILE
    module = BackgroundModule()
    assert "damask" not in [i.id for i in module.list_items()]  # not installed: no switch for it

    keyfile.parent.mkdir(parents=True)
    keyfile.write_text("[app/drey/Damask]\nactive-source='bing'\n")
    assert "damask" in [i.id for i in module.list_items()]
    captured = module.capture(assets_dir)
    assert "bing" in captured["items"]["damask"]["keyfile"]
    keyfile.write_text("changed")
    module.apply(captured, assets_dir)
    assert "bing" in keyfile.read_text()

    shutil.rmtree(background.DAMASK_APP_DIR)
    module.apply(captured, assets_dir)  # not installed: nothing is created
    assert not background.DAMASK_APP_DIR.exists()


# -- extensions and layout (two separate modules) ---------------------------------------------

DASH = "/org/gnome/shell/extensions/dash/"
BLUR = "/org/gnome/shell/extensions/blur/"


class FakeDconf:
    def __init__(self, folders):
        self.folders = folders
        self.log = []

    def __call__(self, *args, stdin=None):
        self.log.append(args[0])
        if args[0] == "dump":
            return self.folders.get(args[1], "")
        if args[0] == "reset":
            self.folders.pop(args[2], None)
            return ""
        if args[0] == "load":
            self.folders[args[1]] = stdin
            return ""
        raise AssertionError(args)


@pytest.fixture
def installed_extensions(monkeypatch):
    names = {"dash@x.org": "Dash", "blur@y.org": "Blur"}
    monkeypatch.setattr(shell_extensions, "installed", lambda: names)
    return names


@pytest.fixture
def dconf(monkeypatch):
    fake = FakeDconf({DASH: "[/]\ndock-position='BOTTOM'\n"})
    monkeypatch.setattr(extension_prefs, "_dconf", fake)
    return fake


def test_extension_menu_offers_three_choices(installed_extensions):
    items = ExtensionsModule().list_items()
    visible = [i for i in items if not i.hidden]
    assert [i.name for i in visible] == ["Blur", "Dash"]
    assert [m.label for m in visible[0].modes] == ["Extension and prefs", "Only extension", "Off"]
    assert [m.off for m in visible[0].modes] == [(), ("blur@y.org#prefs",), ("blur@y.org", "blur@y.org#prefs")]
    assert {i.id for i in items if i.hidden} == {"blur@y.org#prefs", "dash@x.org#prefs"}


def test_extension_choices_reach_the_catalog(installed_extensions):
    catalog = registry_module.build_catalog({"extensions": ExtensionsModule()})["extensions"]
    dash = next(e for e in catalog if e["id"] == "dash@x.org")
    assert dash["modes"][1] == {"label": "Only extension", "off": ["dash@x.org#prefs"]}
    assert next(e for e in catalog if e["id"] == "dash@x.org#prefs")["hidden"] is True


def test_extension_and_prefs(assets_dir, installed_extensions, dconf):
    shell = gs("org.gnome.shell")
    shell.set_strv("enabled-extensions", ["dash@x.org", "gone@z.org"])
    module = ExtensionsModule()

    captured = module.capture(assets_dir)
    assert captured["items"] == {
        "dash@x.org": {"enabled": True},
        "dash@x.org#prefs": {"settings": "[/]\ndock-position='BOTTOM'\n"},
        "blur@y.org": {"enabled": False},
    }

    shell.set_strv("enabled-extensions", ["blur@y.org", "gone@z.org"])
    dconf.folders[DASH] = "[/]\ndock-position='LEFT'\n"
    module.apply(captured, assets_dir)
    assert set(shell.get_strv("enabled-extensions")) == {"dash@x.org", "gone@z.org"}  # unknown one left alone
    assert dconf.folders[DASH] == "[/]\ndock-position='BOTTOM'\n"


def test_only_extension_leaves_its_preferences_alone(assets_dir, installed_extensions, dconf):
    shell = gs("org.gnome.shell")
    shell.set_strv("enabled-extensions", ["dash@x.org"])
    module = ExtensionsModule()
    captured = module.capture(assets_dir)

    shell.set_strv("enabled-extensions", [])
    dconf.folders[DASH] = "[/]\ndock-position='LEFT'\n"
    module.apply(captured, assets_dir, disabled_items={"dash@x.org#prefs"})  # the "Only extension" choice
    assert list(shell.get_strv("enabled-extensions")) == ["dash@x.org"]
    assert "LEFT" in dconf.folders[DASH]
    assert "dash@x.org#prefs" not in module.capture(assets_dir, disabled_items={"dash@x.org#prefs"})["items"]


def test_off_leaves_the_extension_completely_alone(assets_dir, installed_extensions, dconf):
    shell = gs("org.gnome.shell")
    shell.set_strv("enabled-extensions", ["dash@x.org"])
    module = ExtensionsModule()
    captured = module.capture(assets_dir)

    shell.set_strv("enabled-extensions", [])
    dconf.folders[DASH] = "[/]\ndock-position='LEFT'\n"
    module.apply(captured, assets_dir, disabled_items={"dash@x.org", "dash@x.org#prefs"})  # the "Off" choice
    assert list(shell.get_strv("enabled-extensions")) == []
    assert "LEFT" in dconf.folders[DASH]


def test_extension_on_off_still_syncs_without_dconf(assets_dir, installed_extensions, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(extension_prefs.subprocess, "run", missing)
    gs("org.gnome.shell").set_strv("enabled-extensions", ["dash@x.org"])
    captured = ExtensionsModule().capture(assets_dir)
    assert captured["items"]["dash@x.org"] == {"enabled": True}
    assert "dash@x.org#prefs" not in captured["items"]


def test_layout_is_one_switch_and_syncs_settings_but_never_enabled_state(assets_dir, installed_extensions, dconf):
    shell = gs("org.gnome.shell")
    shell.set_strv("enabled-extensions", ["dash@x.org"])
    module = LayoutModule()
    assert len(module.list_items()) == 1  # no per-extension drill-down

    captured = module.capture(assets_dir)
    assert captured["items"]["all"] == {"settings": {"dash@x.org": "[/]\ndock-position='BOTTOM'\n"}}  # Blur has none

    dconf.folders[DASH] = "[/]\ndock-position='LEFT'\n"
    module.apply(captured, assets_dir)
    assert dconf.folders[DASH] == "[/]\ndock-position='BOTTOM'\n"
    assert list(shell.get_strv("enabled-extensions")) == ["dash@x.org"]  # on/off untouched

    dconf.log.clear()
    module.apply(captured, assets_dir)
    assert dconf.log == ["dump"]  # already identical: nothing is rewritten


def test_layout_keeps_settings_of_extensions_missing_here():
    merged = LayoutModule().merge_item(
        "all",
        {"settings": {"only-there@x": "[/]\na=1\n", "dash@x.org": "old"}},
        {"settings": {"dash@x.org": "new"}},
    )
    assert merged == {"settings": {"only-there@x": "[/]\na=1\n", "dash@x.org": "new"}}


def test_layout_skips_settings_for_extensions_not_installed_here(assets_dir, installed_extensions, dconf):
    LayoutModule().apply({"items": {"all": {"settings": {"missing@x": "[/]\na=1\n"}}}}, assets_dir)
    assert "/org/gnome/shell/extensions/missing/" not in dconf.folders


def test_layout_unavailable_without_dconf(assets_dir, installed_extensions, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(extension_prefs.subprocess, "run", missing)
    with pytest.raises(ModuleUnavailable):
        LayoutModule().capture(assets_dir)


# -- power --------------------------------------------------------------------------------------

POWER = "org.gnome.settings-daemon.plugins.power"


def test_power_items_mirror_the_control_center_rows():
    assert [i.id for i in PowerModule().list_items()] == [
        "power_button", "battery_percentage", "dim_screen", "auto_brightness",
        "auto_power_saver", "screen_blank", "suspend_battery", "suspend_plugged"]


def test_power_roundtrip_and_items_are_independent(assets_dir):
    power, session, interface = gs(POWER), gs("org.gnome.desktop.session"), gs("org.gnome.desktop.interface")
    power.set_string("power-button-action", "hibernate")
    power.set_string("sleep-inactive-battery-type", "suspend")
    power.set_int("sleep-inactive-battery-timeout", 600)
    power.set_string("sleep-inactive-ac-type", "nothing")
    session.set_uint("idle-delay", 300)
    interface.set_boolean("show-battery-percentage", True)

    def mutate():
        power.set_string("power-button-action", "nothing")
        power.set_int("sleep-inactive-battery-timeout", 1800)
        session.set_uint("idle-delay", 0)
        interface.set_boolean("show-battery-percentage", False)

    roundtrip(PowerModule(), assets_dir, mutate)
    assert power.get_string("power-button-action") == "hibernate"
    assert power.get_int("sleep-inactive-battery-timeout") == 600
    assert session.get_uint("idle-delay") == 300
    assert interface.get_boolean("show-battery-percentage") is True

    captured = PowerModule().capture(assets_dir)
    power.set_int("sleep-inactive-battery-timeout", 1800)
    power.set_string("power-button-action", "nothing")
    PowerModule().apply(captured, assets_dir, disabled_items={"suspend_battery"})
    assert power.get_int("sleep-inactive-battery-timeout") == 1800  # that row is switched off here
    assert power.get_string("power-button-action") == "hibernate"


def test_layout_and_power_start_switched_off():
    assert LayoutModule.default_enabled is False and PowerModule.default_enabled is False
    assert all(cls.default_enabled for mid, cls in REGISTRY.items() if mid not in ("layout", "power"))


# -- profile picture ----------------------------------------------------------------------------

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
