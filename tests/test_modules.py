"""Every module: capture -> change things -> apply restores, and a second
capture is identical (otherwise the background service would push forever)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from gi.repository import Gio, GLib

from asinux_sync import registry as registry_module
from asinux_sync.modules import REGISTRY, background, extension_prefs, profile_picture, shell_extensions, shortcuts
from asinux_sync.modules import region as region_module
from asinux_sync.modules.accessibility import AccessibilityModule
from asinux_sync.modules.datetime_settings import DateTimeModule
from asinux_sync.modules.region import RegionModule
from asinux_sync.modules.appearance import AppearanceModule
from asinux_sync.modules.background import BackgroundModule
from asinux_sync.modules.base import ModuleUnavailable
from asinux_sync.modules.default_apps import DefaultAppsModule
from asinux_sync.modules.dock import DockModule
from asinux_sync.modules.extensions import ExtensionsModule
from asinux_sync.modules.layout import LayoutModule
from asinux_sync.modules.fonts import FontsModule
from asinux_sync.modules.night_light import NightLightModule
from asinux_sync.modules.search import SearchModule
from asinux_sync.modules.app_grid import AppGridModule
from asinux_sync.modules import mouse, notifications
from asinux_sync.modules.keyboard import KeyboardModule
from asinux_sync.modules.mouse import MouseModule
from asinux_sync.modules.multitasking import MultitaskingModule
from asinux_sync.modules.notifications import NotificationsModule
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
        "dash@x.org#prefs": {"settings": {DASH: "[/]\ndock-position='BOTTOM'\n"}},
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
    assert captured["items"]["all"] == {"settings": {"dash@x.org": {DASH: "[/]\ndock-position='BOTTOM'\n"}}}  # Blur has none

    dconf.folders[DASH] = "[/]\ndock-position='LEFT'\n"
    module.apply(captured, assets_dir)
    assert dconf.folders[DASH] == "[/]\ndock-position='BOTTOM'\n"
    assert list(shell.get_strv("enabled-extensions")) == ["dash@x.org"]  # on/off untouched

    dconf.log.clear()
    module.apply(captured, assets_dir)
    assert dconf.log == ["dump"]  # already identical: nothing is rewritten


def make_extension(root, uuid, schema_paths):
    folder = root / uuid
    (folder / "schemas").mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"uuid": uuid, "name": uuid}))
    body = "".join(f'<schema id="org.x.s{i}" path="{p}"/>' for i, p in enumerate(schema_paths))
    body += '<schema id="org.x.relocatable"/>'  # no path: nothing to read
    (folder / "schemas" / "x.gschema.xml").write_text(f"<schemalist>{body}</schemalist>")


def test_extension_settings_are_found_where_the_extension_really_keeps_them(tmp_path, monkeypatch):
    make_extension(tmp_path, "ddterm@amezin.github.com", ["/com/github/amezin/ddterm/"])
    make_extension(tmp_path, "just-perfection-desktop@just-perfection", ["/org/gnome/shell/extensions/just-perfection/"])
    make_extension(tmp_path, "two@x", ["/org/gnome/shell/extensions/two/", "/org/example/two-extra"])
    (tmp_path / "bare@x").mkdir()
    (tmp_path / "bare@x" / "metadata.json").write_text(json.dumps({"uuid": "bare@x", "name": "Bare"}))
    monkeypatch.setattr(shell_extensions, "_extension_dirs", lambda: [tmp_path])

    assert extension_prefs.paths("ddterm@amezin.github.com") == ["/com/github/amezin/ddterm/"]
    assert extension_prefs.paths("just-perfection-desktop@just-perfection") == ["/org/gnome/shell/extensions/just-perfection/"]
    assert extension_prefs.paths("two@x") == ["/org/gnome/shell/extensions/two/", "/org/example/two-extra/"]
    assert extension_prefs.paths("bare@x") == ["/org/gnome/shell/extensions/bare/"]  # no schema: the convention
    assert extension_prefs.paths("not-installed@x") == ["/org/gnome/shell/extensions/not-installed/"]


def test_subfolders_of_a_declared_folder_are_not_listed_again(tmp_path, monkeypatch):
    make_extension(tmp_path, "copyous@x", [
        "/org/gnome/shell/extensions/copyous/", "/org/gnome/shell/extensions/copyous/file-item/",
        "/org/gnome/shell/extensions/copyous/link-item/"])
    monkeypatch.setattr(shell_extensions, "_extension_dirs", lambda: [tmp_path])
    assert extension_prefs.paths("copyous@x") == ["/org/gnome/shell/extensions/copyous/"]


def test_a_schema_path_that_is_too_broad_is_ignored(tmp_path, monkeypatch):
    make_extension(tmp_path, "evil@x", ["/org/gnome/shell/", "/org/gnome/", "/org/gnome/shell/extensions/"])
    monkeypatch.setattr(shell_extensions, "_extension_dirs", lambda: [tmp_path])
    assert extension_prefs.paths("evil@x") == ["/org/gnome/shell/extensions/evil/"]


def test_layout_reaches_extensions_with_non_standard_paths(assets_dir, tmp_path, monkeypatch):
    make_extension(tmp_path, "ddterm@amezin.github.com", ["/com/github/amezin/ddterm/"])
    monkeypatch.setattr(shell_extensions, "_extension_dirs", lambda: [tmp_path])
    ddterm = "/com/github/amezin/ddterm/"
    fake = FakeDconf({ddterm: "[/]\nwindow-size=0.5\n"})
    monkeypatch.setattr(extension_prefs, "_dconf", fake)

    captured = LayoutModule().capture(assets_dir)
    assert captured["items"]["all"]["settings"] == {"ddterm@amezin.github.com": {ddterm: "[/]\nwindow-size=0.5\n"}}
    fake.folders[ddterm] = "[/]\nwindow-size=0.9\n"
    LayoutModule().apply(captured, assets_dir)
    assert fake.folders[ddterm] == "[/]\nwindow-size=0.5\n"


def test_a_profile_cannot_write_outside_the_extensions_own_folders(tmp_path, monkeypatch):
    make_extension(tmp_path, "dash@x.org", ["/org/gnome/shell/extensions/dash-to-dock/"])
    monkeypatch.setattr(shell_extensions, "_extension_dirs", lambda: [tmp_path])
    own, foreign = "/org/gnome/shell/extensions/dash-to-dock/", "/org/gnome/desktop/interface/"
    fake = FakeDconf({})
    monkeypatch.setattr(extension_prefs, "_dconf", fake)
    extension_prefs.apply("dash@x.org", {own: "[/]\na=1\n", foreign: "[/]\ngtk-theme='evil'\n"})
    assert own in fake.folders and foreign not in fake.folders


def test_the_single_folder_format_of_earlier_versions_is_still_applied(installed_extensions, dconf):
    dconf.folders.clear()
    extension_prefs.apply("dash@x.org", "[/]\ndock-position='LEFT'\n")
    assert dconf.folders[DASH] == "[/]\ndock-position='LEFT'\n"


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


# -- multitasking ---------------------------------------------------------------------------------

def test_multitasking_items_mirror_the_control_center_rows():
    assert [i.id for i in MultitaskingModule().list_items()] == [
        "hot_corner", "window_resize", "reopen_windows", "workspace_mode", "workspace_count",
        "multi_monitor", "app_switching"]


def test_multitasking_roundtrip_and_items_are_independent(assets_dir):
    interface, mutter = gs("org.gnome.desktop.interface"), gs("org.gnome.mutter")
    wm, session = gs("org.gnome.desktop.wm.preferences"), gs("org.gnome.desktop.session")
    switcher = gs("org.gnome.shell.app-switcher")
    interface.set_boolean("enable-hot-corners", False)
    mutter.set_boolean("edge-tiling", False)
    mutter.set_boolean("dynamic-workspaces", False)
    mutter.set_boolean("workspaces-only-on-primary", True)
    wm.set_int("num-workspaces", 4)
    session.set_boolean("save-restore", False)
    switcher.set_boolean("current-workspace-only", True)

    def mutate():
        interface.set_boolean("enable-hot-corners", True)
        mutter.set_boolean("dynamic-workspaces", True)
        wm.set_int("num-workspaces", 2)
        switcher.set_boolean("current-workspace-only", False)

    roundtrip(MultitaskingModule(), assets_dir, mutate)
    assert interface.get_boolean("enable-hot-corners") is False
    assert wm.get_int("num-workspaces") == 4
    assert switcher.get_boolean("current-workspace-only") is True

    captured = MultitaskingModule().capture(assets_dir)
    interface.set_boolean("enable-hot-corners", True)
    wm.set_int("num-workspaces", 9)
    MultitaskingModule().apply(captured, assets_dir, disabled_items={"workspace_count"})
    assert interface.get_boolean("enable-hot-corners") is False  # applied
    assert wm.get_int("num-workspaces") == 9  # that row is switched off here


# -- every GSettings key an item names must exist ---------------------------------------------------

def test_every_gsettings_key_named_by_a_module_exists():
    from asinux_sync.modules import gsettings_util
    from asinux_sync.modules.gsettings_module import GSettingsModule

    problems = []
    for module_id, cls in REGISTRY.items():
        if not issubclass(cls, GSettingsModule):
            continue
        for group in cls().group_specs():
            for schema_id, keys in group.schemas.items():
                schema = gsettings_util.schema(schema_id)
                if schema is None:
                    continue  # an optional component (Hanabi, User Themes, Tracker) isn't installed here
                problems += [f"{module_id}.{group.id}: {schema_id} {k}" for k in keys if not schema.has_key(k)]
    assert problems == []


# -- fonts, night light, search, app grid ------------------------------------------------------------

def test_fonts_items_and_roundtrip(assets_dir):
    assert [i.id for i in FontsModule().list_items()] == [
        "interface_font", "document_font", "monospace_font", "titlebar_font", "hinting", "antialiasing"]
    interface, wm = gs("org.gnome.desktop.interface"), gs("org.gnome.desktop.wm.preferences")
    interface.set_string("font-name", "Inter 11")
    interface.set_string("monospace-font-name", "JetBrains Mono 10")
    interface.set_string("font-hinting", "full")
    wm.set_string("titlebar-font", "Inter Bold 11")
    wm.set_boolean("titlebar-uses-system-font", False)

    def mutate():
        interface.set_string("font-name", "Cantarell 11")
        interface.set_string("font-hinting", "none")
        wm.set_string("titlebar-font", "Cantarell Bold 11")

    roundtrip(FontsModule(), assets_dir, mutate)
    assert interface.get_string("font-name") == "Inter 11" and interface.get_string("font-hinting") == "full"
    assert wm.get_string("titlebar-font") == "Inter Bold 11"

    captured = FontsModule().capture(assets_dir)
    interface.set_string("font-name", "Cantarell 11")
    FontsModule().apply(captured, assets_dir, disabled_items={"interface_font"})
    assert interface.get_string("font-name") == "Cantarell 11"  # switched off here
    assert interface.get_string("monospace-font-name") == "JetBrains Mono 10"


def test_fonts_leave_the_text_scaling_factor_to_accessibility():
    keys = {k for g in FontsModule.groups for ks in g.schemas.values() for k in ks}
    assert "text-scaling-factor" not in keys


def test_night_light_roundtrip(assets_dir):
    color = gs("org.gnome.settings-daemon.plugins.color")
    color.set_boolean("night-light-enabled", True)
    color.set_boolean("night-light-schedule-automatic", False)
    color.set_double("night-light-schedule-from", 21.5)
    color.set_uint("night-light-temperature", 3200)

    def mutate():
        color.set_boolean("night-light-enabled", False)
        color.set_double("night-light-schedule-from", 19.0)
        color.set_uint("night-light-temperature", 4000)

    roundtrip(NightLightModule(), assets_dir, mutate)
    assert color.get_boolean("night-light-enabled") is True
    assert color.get_double("night-light-schedule-from") == pytest.approx(21.5)
    assert color.get_uint("night-light-temperature") == 3200
    assert "night-light-last-coordinates" not in {
        k for g in NightLightModule.groups for ks in g.schemas.values() for k in ks}


def test_search_roundtrip_and_items(assets_dir):
    assert [i.id for i in SearchModule().list_items()] == ["app_search", "providers", "result_order", "locations"]
    providers = gs("org.gnome.desktop.search-providers")
    providers.set_strv("disabled", ["org.gnome.Software.desktop"])
    providers.set_strv("sort-order", ["b.desktop", "a.desktop"])
    providers.set_boolean("disable-external", True)
    tracker = gs("org.freedesktop.Tracker3.Miner.Files")
    tracker.set_strv("index-recursive-directories", ["&DOCUMENTS", "/data"])

    def mutate():
        providers.set_strv("disabled", [])
        providers.set_strv("sort-order", ["a.desktop"])
        providers.set_boolean("disable-external", False)
        tracker.set_strv("index-recursive-directories", ["$HOME"])

    roundtrip(SearchModule(), assets_dir, mutate)
    assert list(providers.get_strv("disabled")) == ["org.gnome.Software.desktop"]
    assert list(providers.get_strv("sort-order")) == ["b.desktop", "a.desktop"]
    assert list(tracker.get_strv("index-recursive-directories")) == ["&DOCUMENTS", "/data"]


def folder_settings(name):
    return gs("org.gnome.desktop.app-folders.folder", f"/org/gnome/desktop/app-folders/folders/{name}/")


def test_app_grid_order_roundtrip(assets_dir):
    shell = gs("org.gnome.shell")
    layout = GLib.Variant.parse(GLib.VariantType("aa{sv}"), "[{'a.desktop': <{'position': <0>}>, 'b.desktop': <{'position': <1>}>}]")
    shell.set_value("app-picker-layout", layout)

    def mutate():
        shell.reset("app-picker-layout")

    roundtrip(AppGridModule(), assets_dir, mutate)
    assert shell.get_value("app-picker-layout").equal(layout)


def test_app_grid_folders_roundtrip_and_cleanup(assets_dir):
    assert [i.id for i in AppGridModule().list_items()] == ["order", "folders"]
    folders = gs("org.gnome.desktop.app-folders")
    folders.set_strv("folder-children", ["Mine", "Games"])
    mine = folder_settings("Mine")
    mine.set_string("name", "My apps")
    mine.set_boolean("translate", False)
    mine.set_strv("apps", ["a.desktop", "b.desktop"])
    folder_settings("Games").set_string("name", "Games")

    def mutate():
        folders.set_strv("folder-children", ["Games", "Other"])
        mine.set_strv("apps", ["z.desktop"])
        folder_settings("Other").set_string("name", "Only here")

    roundtrip(AppGridModule(), assets_dir, mutate)
    assert list(folders.get_strv("folder-children")) == ["Mine", "Games"]
    assert list(mine.get_strv("apps")) == ["a.desktop", "b.desktop"] and mine.get_string("name") == "My apps"


def test_app_grid_folders_dropped_by_the_profile_are_forgotten(assets_dir):
    folders = gs("org.gnome.desktop.app-folders")
    folders.set_strv("folder-children", ["Mine"])
    folder_settings("Mine").set_string("name", "Mine")
    captured = AppGridModule().capture(assets_dir)

    folders.set_strv("folder-children", ["Mine", "Games"])
    folder_settings("Games").set_string("name", "Games")
    AppGridModule().apply(captured, assets_dir)
    assert list(folders.get_strv("folder-children")) == ["Mine"]
    assert folder_settings("Games").get_user_value("name") is None


# -- date and time ----------------------------------------------------------------------------------

def test_datetime_items_and_keys_exist():
    from asinux_sync.modules import gsettings_util

    assert [i.id for i in DateTimeModule().list_items()] == [
        "auto_timezone", "time_format", "first_weekday", "clock_weekday", "clock_date", "clock_seconds", "week_numbers"]
    missing = [(s, k) for g in DateTimeModule.groups for s, ks in g.schemas.items() for k in ks
               if gsettings_util.schema(s) is None or not gsettings_util.schema(s).has_key(k)]
    assert missing == []


def test_datetime_roundtrip_and_items_are_independent(assets_dir):
    interface, calendar = gs("org.gnome.desktop.interface"), gs("org.gnome.desktop.calendar")
    chooser = gs("org.gtk.Settings.FileChooser")
    interface.set_string("clock-format", "24h")
    chooser.set_string("clock-format", "24h")
    interface.set_boolean("clock-show-seconds", False)
    calendar.set_string("week-start-day", "sunday")
    calendar.set_boolean("show-weekdate", False)
    gs("org.gnome.desktop.datetime").set_boolean("automatic-timezone", True)

    def mutate():
        interface.set_string("clock-format", "12h")
        chooser.set_string("clock-format", "12h")
        interface.set_boolean("clock-show-seconds", True)
        calendar.set_string("week-start-day", "monday")

    roundtrip(DateTimeModule(), assets_dir, mutate)
    assert interface.get_string("clock-format") == "24h" and chooser.get_string("clock-format") == "24h"
    assert interface.get_boolean("clock-show-seconds") is False
    assert calendar.get_string("week-start-day") == "sunday"

    captured = DateTimeModule().capture(assets_dir)
    calendar.set_string("week-start-day", "monday")
    interface.set_string("clock-format", "12h")
    DateTimeModule().apply(captured, assets_dir, disabled_items={"first_weekday"})
    assert calendar.get_string("week-start-day") == "monday"  # switched off here
    assert interface.get_string("clock-format") == "24h"


# -- region and language -----------------------------------------------------------------------------

class FakeAccounts:
    def __init__(self, languages):
        self.languages = list(languages)
        self.calls = []

    def get_cached_property(self, name):
        return GLib.Variant("as", self.languages)

    def call_sync(self, method, args, *_):
        assert method == "SetLanguages"
        self.languages = list(args.unpack()[0])
        self.calls.append(self.languages)


@pytest.fixture
def accounts(monkeypatch):
    fake = FakeAccounts(["en_US.UTF-8"])
    monkeypatch.setattr(region_module, "_user_proxy", lambda: fake)
    monkeypatch.setattr(region_module, "_locale_installed", lambda name: name != "xx_XX.UTF-8")
    return fake


def test_region_items_are_language_and_formats():
    assert [i.id for i in RegionModule().list_items()] == ["language", "formats"]


def test_region_roundtrip(assets_dir, accounts):
    locale = gs("org.gnome.system.locale")
    accounts.languages = ["pl_PL.UTF-8", "de_DE.UTF-8"]
    locale.set_string("region", "de_DE.UTF-8")
    module = RegionModule()
    captured = module.capture(assets_dir)
    assert captured["items"] == {"language": {"language": "pl_PL.UTF-8"}, "formats": {"region": "de_DE.UTF-8"}}

    accounts.languages = ["en_US.UTF-8"]
    locale.reset("region")
    module.apply(captured, assets_dir)
    assert accounts.languages == ["pl_PL.UTF-8", "de_DE.UTF-8"]  # [language, formats], like the panel stores it
    assert locale.get_string("region") == "de_DE.UTF-8"
    assert module.capture(assets_dir) == captured


def test_region_formats_equal_to_language_are_stored_once(assets_dir, accounts):
    RegionModule().apply({"items": {"language": {"language": "pl_PL.UTF-8"}, "formats": {"region": "pl_PL.UTF-8"}}}, assets_dir)
    assert accounts.languages == ["pl_PL.UTF-8"]


def test_region_clearing_formats_resets_the_key(assets_dir, accounts):
    locale = gs("org.gnome.system.locale")
    locale.set_string("region", "de_DE.UTF-8")
    accounts.languages = ["pl_PL.UTF-8", "de_DE.UTF-8"]
    RegionModule().apply({"items": {"formats": {"region": ""}}}, assets_dir)
    assert locale.get_user_value("region") is None and accounts.languages == ["pl_PL.UTF-8"]


def test_region_skips_a_locale_that_is_not_installed(assets_dir, accounts):
    RegionModule().apply({"items": {"language": {"language": "xx_XX.UTF-8"}, "formats": {"region": "xx_XX.UTF-8"}}}, assets_dir)
    assert accounts.calls == [] and accounts.languages == ["en_US.UTF-8"]
    assert gs("org.gnome.system.locale").get_user_value("region") is None


def test_region_items_can_be_switched_off_separately(assets_dir, accounts):
    accounts.languages = ["pl_PL.UTF-8"]
    captured = RegionModule().capture(assets_dir)
    accounts.languages = ["en_US.UTF-8"]
    RegionModule().apply(captured, assets_dir, disabled_items={"language"})
    assert accounts.languages == ["en_US.UTF-8"]


def test_region_locale_check_uses_locale_a(monkeypatch):
    class Done:
        stdout = "C\nen_US.utf8\npl_PL.utf8\n"

    monkeypatch.setattr(region_module.subprocess, "run", lambda *a, **k: Done())
    assert region_module._locale_installed("pl_PL.UTF-8") is True
    assert region_module._locale_installed("de_DE.UTF-8") is False


# -- accessibility --------------------------------------------------------------------------------

def test_accessibility_items_mirror_the_control_center_pages():
    ids = [i.id for i in AccessibilityModule().list_items()]
    assert ids[0] == "menu" and "screen_reader" in ids and "bounce_keys" in ids and ids[-1] == "color_filters"
    assert len(ids) == 25
    # Keys another module already owns must not be synced twice.
    owned_elsewhere = {"cursor-size", "repeat", "delay", "repeat-interval", "focus-mode"}
    keys = {k for g in AccessibilityModule.groups for ks in g.schemas.values() for k in ks}
    assert not owned_elsewhere & keys


def test_every_accessibility_key_exists_in_the_installed_schemas():
    from asinux_sync.modules import gsettings_util

    missing = [(s, k) for g in AccessibilityModule.groups for s, ks in g.schemas.items() for k in ks
               if gsettings_util.schema(s) is None or not gsettings_util.schema(s).has_key(k)]
    assert missing == []


def test_accessibility_roundtrip_and_items_are_independent(assets_dir):
    iface, kbd = gs("org.gnome.desktop.a11y.interface"), gs("org.gnome.desktop.a11y.keyboard")
    mag, mouse_a = gs("org.gnome.desktop.a11y.magnifier"), gs("org.gnome.desktop.a11y.mouse")
    gs("org.gnome.desktop.interface").set_double("text-scaling-factor", 1.25)
    iface.set_boolean("high-contrast", True)
    iface.set_string("reduced-motion", "reduce")
    kbd.set_boolean("stickykeys-enable", True)
    kbd.set_int("slowkeys-delay", 400)
    mag.set_double("mag-factor", 3.0)
    mag.set_boolean("show-cross-hairs", True)
    mouse_a.set_boolean("dwell-click-enabled", True)
    gs("org.gnome.desktop.peripherals.mouse").set_int("double-click", 700)

    def mutate():
        gs("org.gnome.desktop.interface").set_double("text-scaling-factor", 1.0)
        iface.set_boolean("high-contrast", False)
        kbd.set_int("slowkeys-delay", 300)
        mag.set_double("mag-factor", 1.5)

    roundtrip(AccessibilityModule(), assets_dir, mutate)
    assert gs("org.gnome.desktop.interface").get_double("text-scaling-factor") == pytest.approx(1.25)
    assert iface.get_boolean("high-contrast") is True and kbd.get_int("slowkeys-delay") == 400
    assert mag.get_double("mag-factor") == pytest.approx(3.0)
    assert gs("org.gnome.desktop.peripherals.mouse").get_int("double-click") == 700

    captured = AccessibilityModule().capture(assets_dir)
    iface.set_boolean("high-contrast", False)
    mag.set_double("mag-factor", 1.5)
    AccessibilityModule().apply(captured, assets_dir, disabled_items={"high_contrast"})
    assert iface.get_boolean("high-contrast") is False  # that item is switched off here
    assert mag.get_double("mag-factor") == pytest.approx(3.0)


# -- keyboard ----------------------------------------------------------------------------------------

INPUT_S = "org.gnome.desktop.input-sources"


def test_keyboard_items():
    assert [i.id for i in KeyboardModule().list_items()] == [
        "input_sources", "per_window", "alternate_key", "compose_key", "layout_options",
        "key_repeat", "emacs_input", "overview_key"]


def test_keyboard_roundtrip(assets_dir):
    inp, kb = gs(INPUT_S), gs("org.gnome.desktop.peripherals.keyboard")
    inp.set_value("sources", GLib.Variant("a(ss)", [("xkb", "pl"), ("xkb", "us+intl")]))
    inp.set_boolean("per-window", True)
    inp.set_strv("xkb-options", ["lv3:ralt_switch", "compose:ralt", "caps:escape"])
    kb.set_uint("delay", 300)
    kb.set_uint("repeat-interval", 20)
    gs("org.gnome.desktop.interface").set_string("gtk-key-theme", "Emacs")
    gs("org.gnome.mutter").set_string("overlay-key", "")

    def mutate():
        inp.set_value("sources", GLib.Variant("a(ss)", [("xkb", "de")]))
        inp.set_boolean("per-window", False)
        inp.set_strv("xkb-options", ["compose:menu", "ctrl:nocaps"])
        kb.set_uint("delay", 500)

    roundtrip(KeyboardModule(), assets_dir, mutate)
    assert inp.get_value("sources").unpack() == [("xkb", "pl"), ("xkb", "us+intl")]
    assert inp.get_boolean("per-window") is True
    assert set(inp.get_strv("xkb-options")) == {"lv3:ralt_switch", "compose:ralt", "caps:escape"}
    assert kb.get_uint("delay") == 300
    assert gs("org.gnome.desktop.interface").get_string("gtk-key-theme") == "Emacs"
    assert gs("org.gnome.mutter").get_string("overlay-key") == ""


def test_each_xkb_item_only_touches_its_own_options(assets_dir):
    inp = gs(INPUT_S)
    inp.set_strv("xkb-options", ["lv3:ralt_switch", "compose:ralt", "caps:escape"])
    module = KeyboardModule()
    captured = module.capture(assets_dir)
    assert captured["items"]["compose_key"] == {"options": ["compose:ralt"]}
    assert captured["items"]["alternate_key"] == {"options": ["lv3:ralt_switch"]}
    assert captured["items"]["layout_options"] == {"options": ["caps:escape"]}

    inp.set_strv("xkb-options", ["compose:menu", "ctrl:nocaps"])
    module.apply({"items": {"compose_key": captured["items"]["compose_key"]}}, assets_dir)
    assert set(inp.get_strv("xkb-options")) == {"compose:ralt", "ctrl:nocaps"}  # only compose changed

    module.apply(captured, assets_dir, disabled_items={"layout_options", "alternate_key"})
    assert set(inp.get_strv("xkb-options")) == {"compose:ralt", "ctrl:nocaps"}


def test_layout_validation_uses_the_xkb_catalog():
    from asinux_sync import system_defaults as sd

    if sd._xkb_catalog() is None:
        pytest.skip("xkeyboard-config rules not installed")
    assert sd.valid_keyboard_layout("pl") and sd.valid_keyboard_layout("us+dvorak")
    assert not sd.valid_keyboard_layout("xx") and not sd.valid_keyboard_layout("pl+nope")
    assert not sd.valid_keyboard_layout("")


def test_apply_keyboard_layout_only_when_different():
    from asinux_sync import system_defaults as sd

    if sd._xkb_catalog() is None:
        pytest.skip("xkeyboard-config rules not installed")
    inp = gs(INPUT_S)
    inp.set_value("sources", GLib.Variant("a(ss)", [("xkb", "pl"), ("xkb", "us")]))
    assert sd.apply_keyboard_layout("pl") is False  # already first: the other sources are kept
    assert len(inp.get_value("sources").unpack()) == 2
    assert sd.apply_keyboard_layout("de") is True
    assert inp.get_value("sources").unpack() == [("xkb", "de")]
    assert sd.apply_keyboard_layout("not-a-layout") is False
    assert inp.get_value("sources").unpack() == [("xkb", "de")]


def test_keyboard_chosen_at_profile_creation_is_the_default(monkeypatch):
    from asinux_sync import account_defaults

    if __import__("asinux_sync.system_defaults", fromlist=["x"])._xkb_catalog() is None:
        pytest.skip("xkeyboard-config rules not installed")
    inp = gs(INPUT_S)

    def current():
        return inp.get_value("sources").unpack()

    inp.set_value("sources", GLib.Variant("a(ss)", [("xkb", "us")]))
    # a new profile: the chosen layout becomes this device's
    assert account_defaults.apply_keyboard_default("pl", new_account=True, disabled=[], cloud_has_keyboard=lambda: True)
    assert current() == [("xkb", "pl")]
    # a device joining a profile whose cloud already has a keyboard: left alone
    assert not account_defaults.apply_keyboard_default("de", new_account=False, disabled=[], cloud_has_keyboard=lambda: True)
    assert current() == [("xkb", "pl")]
    # ...but if nobody has synced one yet, the profile's choice fills the gap
    assert account_defaults.apply_keyboard_default("de", new_account=False, disabled=[], cloud_has_keyboard=lambda: False)
    assert current() == [("xkb", "de")]
    # not when this device doesn't sync keyboard input sources
    for off in (["keyboard"], ["keyboard.input_sources"]):
        assert not account_defaults.apply_keyboard_default("fr", new_account=True, disabled=off, cloud_has_keyboard=lambda: False)
    assert not account_defaults.apply_keyboard_default("", new_account=True, disabled=[], cloud_has_keyboard=lambda: False)
    assert current() == [("xkb", "de")]


# -- mouse and touchpad ----------------------------------------------------------------------------

MOUSE_S, TOUCHPAD_S, STICK_S = mouse.MOUSE, mouse.TOUCHPAD, mouse.POINTING_STICK


def hardware(monkeypatch, touchpad, stick):
    monkeypatch.setattr(mouse, "_has_input_device", lambda prop: {
        "ID_INPUT_TOUCHPAD": touchpad, "ID_INPUT_POINTINGSTICK": stick}[prop])


def test_touchpad_items_exist_only_with_the_hardware(monkeypatch):
    hardware(monkeypatch, touchpad=False, stick=False)
    desktop = [i.id for i in MouseModule().list_items()]
    assert desktop == ["mouse_primary_button", "mouse_speed", "mouse_acceleration",
                       "mouse_scroll_direction", "middle_click_paste"]
    hardware(monkeypatch, touchpad=True, stick=True)
    laptop = [i.id for i in MouseModule().list_items()]
    assert laptop[len(desktop):] == [
        "touchpad_enabled", "touchpad_disable_while_typing", "touchpad_speed", "touchpad_secondary_click",
        "touchpad_tap_to_click", "touchpad_scroll_method", "touchpad_scroll_direction",
        "stick_speed", "stick_acceleration", "stick_scroll_method"]


def test_mouse_roundtrip_and_items_are_independent(assets_dir, monkeypatch):
    hardware(monkeypatch, touchpad=True, stick=True)
    mouse_s, pad, stick = gs(MOUSE_S), gs(TOUCHPAD_S), gs(STICK_S)
    mouse_s.set_boolean("left-handed", True)
    mouse_s.set_double("speed", 0.4)
    mouse_s.set_string("accel-profile", "flat")
    pad.set_boolean("tap-to-click", False)
    pad.set_boolean("natural-scroll", False)
    pad.set_boolean("two-finger-scrolling-enabled", False)
    pad.set_boolean("edge-scrolling-enabled", True)
    pad.set_string("click-method", "areas")
    stick.set_double("speed", -0.3)
    gs("org.gnome.desktop.interface").set_boolean("gtk-enable-primary-paste", False)

    def mutate():
        mouse_s.set_boolean("left-handed", False)
        mouse_s.set_double("speed", 0.9)
        pad.set_boolean("edge-scrolling-enabled", False)
        pad.set_boolean("two-finger-scrolling-enabled", True)
        pad.set_boolean("tap-to-click", True)
        stick.set_double("speed", 0.5)

    roundtrip(MouseModule(), assets_dir, mutate)
    assert mouse_s.get_boolean("left-handed") is True and mouse_s.get_double("speed") == pytest.approx(0.4)
    assert pad.get_boolean("edge-scrolling-enabled") is True
    assert pad.get_boolean("two-finger-scrolling-enabled") is False
    assert pad.get_boolean("tap-to-click") is False
    assert stick.get_double("speed") == pytest.approx(-0.3)
    assert gs("org.gnome.desktop.interface").get_boolean("gtk-enable-primary-paste") is False

    captured = MouseModule().capture(assets_dir)
    pad.set_boolean("tap-to-click", True)
    mouse_s.set_double("speed", 0.9)
    MouseModule().apply(captured, assets_dir, disabled_items={"touchpad_tap_to_click"})
    assert pad.get_boolean("tap-to-click") is True  # that row is switched off here
    assert mouse_s.get_double("speed") == pytest.approx(0.4)


def test_a_machine_without_a_touchpad_neither_uploads_nor_applies_touchpad_settings(assets_dir, monkeypatch):
    hardware(monkeypatch, touchpad=False, stick=False)
    pad = gs(TOUCHPAD_S)
    pad.set_double("speed", 0.7)
    module = MouseModule()
    assert not any(item.startswith("touchpad") for item in module.capture(assets_dir)["items"])
    # A profile from a laptop with a touchpad left at its defaults must not reset ours.
    module.apply({"items": {"touchpad_speed": {"values": {TOUCHPAD_S: {}}}}}, assets_dir)
    assert pad.get_double("speed") == pytest.approx(0.7)


# -- notifications ----------------------------------------------------------------------------------

NOTIFY = "org.gnome.desktop.notifications"
NOTIFY_APP = NOTIFY + ".application"


def app_settings(child):
    return gs(NOTIFY_APP, f"/org/gnome/desktop/notifications/application/{child}/")


@pytest.fixture
def two_apps(monkeypatch):
    gs(NOTIFY).set_strv("application-children", ["app-b", "app-a"])
    app_settings("app-a").set_string("application-id", "a.desktop")
    app_settings("app-b").set_string("application-id", "b.desktop")
    names = {"a.desktop": "Zeta app", "b.desktop": "Alpha app"}
    monkeypatch.setattr(notifications, "_app_name", lambda child, application_id: names.get(application_id, child))
    return names


def test_notifications_items_are_the_panel_switches_then_each_app(assets_dir, two_apps):
    items = NotificationsModule().list_items()
    assert [i.id for i in items] == ["do_not_disturb", "lock_screen", "app:app-b", "app:app-a"]
    assert [i.name for i in items[2:]] == ["Alpha app", "Zeta app"]  # sorted by name


def test_notifications_roundtrip(assets_dir, two_apps):
    notify = gs(NOTIFY)
    notify.set_boolean("show-banners", False)
    notify.set_boolean("show-in-lock-screen", False)
    app_a = app_settings("app-a")
    app_a.set_boolean("enable-sound-alerts", False)
    app_a.set_boolean("details-in-lock-screen", True)

    def mutate():
        notify.set_boolean("show-banners", True)
        app_a.set_boolean("enable-sound-alerts", True)
        app_a.set_boolean("details-in-lock-screen", False)
        app_settings("app-b").set_boolean("enable", False)

    roundtrip(NotificationsModule(), assets_dir, mutate)
    assert notify.get_boolean("show-banners") is False
    assert app_a.get_boolean("enable-sound-alerts") is False
    assert app_a.get_boolean("details-in-lock-screen") is True
    assert app_settings("app-b").get_user_value("enable") is None  # not set in the profile: reset


def test_notifications_survive_uninstalled_apps():
    # The real Gio call, not a stub: it raises for a .desktop file that doesn't exist.
    assert notifications._desktop_info("definitely-not-installed.desktop") is None
    assert notifications._app_name("gone-app", "definitely-not-installed.desktop") == "gone-app"
    assert notifications._app_installed("definitely-not-installed.desktop") is False
    assert notifications._app_name("no-id", "") == "no-id"


def test_notifications_one_app_can_be_switched_off(assets_dir, two_apps):
    app_a = app_settings("app-a")
    app_a.set_boolean("enable", False)
    captured = NotificationsModule().capture(assets_dir)
    app_a.set_boolean("enable", True)
    NotificationsModule().apply(captured, assets_dir, disabled_items={"app:app-a"})
    assert app_a.get_boolean("enable") is True
    assert "app:app-a" not in NotificationsModule().capture(assets_dir, disabled_items={"app:app-a"})["items"]


def test_notifications_set_up_an_installed_app_not_known_here_yet(assets_dir, monkeypatch):
    monkeypatch.setattr(notifications, "_app_installed", lambda application_id: application_id == "x.desktop")
    monkeypatch.setattr(notifications, "_app_name", lambda child, application_id: child)
    data = {"items": {
        "app:org-x-y": {"application_id": "x.desktop", "values": {"enable": "false"}},
        "app:not-here": {"application_id": "gone.desktop", "values": {"enable": "false"}},
    }}
    NotificationsModule().apply(data, assets_dir)
    assert list(gs(NOTIFY).get_strv("application-children")) == ["org-x-y"]  # the missing app is not invented
    assert app_settings("org-x-y").get_boolean("enable") is False
    assert app_settings("org-x-y").get_string("application-id") == "x.desktop"


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
