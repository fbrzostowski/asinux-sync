import re
import threading
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from asinux_sync import config as config_module
from asinux_sync import registry as registry_module
from asinux_sync import system_defaults
from asinux_sync.cloud.rclone import RcloneBackend, RcloneError, is_installed
from asinux_sync.engine import ConflictError, SyncEngine
from asinux_sync.modules import REGISTRY

PROVIDER = "onedrive"
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def _page(title, content, header_end=None):
    view = Adw.ToolbarView()
    header = Adw.HeaderBar()
    for widget in header_end or []:
        header.pack_end(widget)
    view.add_top_bar(header)
    view.set_content(content)
    return Adw.NavigationPage(title=title, child=view)


def _status_page(icon, title, description, child=None):
    status = Adw.StatusPage(icon_name=icon, title=title, description=description)
    if child is not None:
        status.set_child(child)
    return status


def _pill_button(label, handler, *args):
    button = Gtk.Button(label=label, halign=Gtk.Align.CENTER)
    button.add_css_class("suggested-action")
    button.add_css_class("pill")
    button.connect("clicked", handler, *args)
    return button


def _format_time(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "never"


class SyncWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Asinux Sync")
        self.set_default_size(520, 680)

        self.cfg = config_module.load()
        self.modules = {mid: cls() for mid, cls in REGISTRY.items()}
        self.registry = registry_module.Registry(RcloneBackend(self.cfg.remote_name))

        self.nav = Adw.NavigationView()
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.nav)
        self.set_content(self.toast_overlay)

        self._start()

    # -- helpers ----------------------------------------------------------

    def _toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    def _run_async(self, work, on_done):
        def runner():
            try:
                result, error = work(), None
            except (RcloneError, ConflictError, ValueError) as exc:
                result, error = None, exc
            GLib.idle_add(on_done, result, error)

        threading.Thread(target=runner, daemon=True).start()

    def _set_root(self, page):
        self.nav.replace([page])

    def _engine(self):
        return SyncEngine(self.registry.backend, self.modules, config_module.STATE_DIR)

    # -- launch routing ---------------------------------------------------

    def _start(self):
        if not is_installed():
            self._set_root(_page("Asinux Sync", _status_page(
                "dialog-warning-symbolic", "rclone is not installed",
                "Asinux Sync needs rclone to talk to your cloud account.")))
        elif not RcloneBackend.exists(self.cfg.remote_name):
            self._show_login()
        elif not self.cfg.device_id:
            self._show_setup()
        else:
            self._show_main()

    # -- login view: authorize ---------------------------------------------

    def _show_login(self):
        button = _pill_button("Sign in with OneDrive", self._on_sign_in)
        content = _status_page(
            "emblem-synchronizing-symbolic", "Asinux Sync",
            "Keep your wallpaper, themes and other settings the same on all your computers.",
            button)
        self._set_root(_page("Asinux Sync", content))

    def _on_sign_in(self, button):
        button.set_sensitive(False)
        button.set_label("Waiting for sign-in in the browser…")

        def on_auth_url(url):
            GLib.idle_add(self._show_auth_url, url)

        def work():
            result = RcloneBackend.authorize(PROVIDER, on_auth_url)
            RcloneBackend.config_create(self.cfg.remote_name, PROVIDER, result.token_json)

        def done(_result, error):
            if error:
                self._toast(f"Sign-in failed: {error}")
                button.set_sensitive(True)
                button.set_label("Sign in with OneDrive")
            else:
                config_module.save(self.cfg)
                self._show_setup()
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)

    def _show_auth_url(self, url):
        dialog = Adw.AlertDialog(
            heading="Sign in to OneDrive",
            body=f"If the browser did not open, open this address in a private window:\n\n{url}",
        )
        dialog.add_response("close", "Close")
        dialog.present(self)
        return GLib.SOURCE_REMOVE

    # -- login view: device name, account, locale ---------------------------

    def _show_setup(self):
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, width_request=32, height_request=32)
        self._set_root(_page("Asinux Sync", _status_page(
            "emblem-synchronizing-symbolic", "Checking your OneDrive…", "", spinner)))

        def work():
            if not self.registry.root_exists():
                return None
            return self.registry.load_account() or {}

        def done(account, error):
            if error:
                self._toast(f"Couldn't reach OneDrive: {error}")
                self._show_login()
            else:
                self._build_setup_form(account)
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)

    def _build_setup_form(self, existing_account):
        """existing_account is None when asinux-sync doesn't exist in the
        cloud yet (first device ever): then also ask for the account details
        and locale defaults. Otherwise only the device name is needed."""
        page = Adw.PreferencesPage()

        device_group = Adw.PreferencesGroup(
            title="This device", description="How this computer appears in your device list.")
        self.device_name_row = Adw.EntryRow(title="Device name")
        self.device_name_row.set_text(self.cfg.device_name or system_defaults.hostname())
        device_group.add(self.device_name_row)
        page.add(device_group)

        is_new = existing_account is None
        if is_new:
            account_group = Adw.PreferencesGroup(
                title="Your account", description="Saved in your OneDrive for your other computers.")
            self.username_row = Adw.EntryRow(title="Username")
            self.username_row.set_text(system_defaults.username())
            self.pretty_name_row = Adw.EntryRow(title="Display name")
            self.pretty_name_row.set_text(system_defaults.pretty_name())
            account_group.add(self.username_row)
            account_group.add(self.pretty_name_row)
            page.add(account_group)

            locale_group = Adw.PreferencesGroup(title="Language and region")
            self.language_row = Adw.EntryRow(title="Language")
            self.language_row.set_text(system_defaults.language())
            self.region_row = Adw.EntryRow(title="Region")
            self.region_row.set_text(system_defaults.region())
            self.keyboard_row = Adw.EntryRow(title="Keyboard layout")
            self.keyboard_row.set_text(system_defaults.keyboard_layout())
            for row in (self.language_row, self.region_row, self.keyboard_row):
                locale_group.add(row)
            page.add(locale_group)
        else:
            found = existing_account.get("pretty_name") or existing_account.get("username") or "your account"
            page.add(Adw.PreferencesGroup(
                description=f"Found existing Asinux Sync data for {found}. "
                            "This computer will be added to it."))

        action_group = Adw.PreferencesGroup()
        action_group.add(_pill_button("Continue", self._on_setup_continue, is_new))
        page.add(action_group)

        self._set_root(_page("Set up this device", page))

    def _on_setup_continue(self, button, is_new):
        device_name = self.device_name_row.get_text().strip()
        if not device_name:
            self._toast("Enter a name for this device")
            return

        account = None
        if is_new:
            username = self.username_row.get_text().strip()
            if not USERNAME_RE.match(username):
                self._toast("Username may only use lowercase letters, digits, - and _")
                return
            account = {
                "username": username,
                "pretty_name": self.pretty_name_row.get_text().strip() or username,
                "language": self.language_row.get_text().strip(),
                "region": self.region_row.get_text().strip(),
                "keyboard_layout": self.keyboard_row.get_text().strip(),
                "created_at": registry_module.now_iso(),
            }

        button.set_sensitive(False)
        self.cfg.device_name = device_name

        def work():
            if account is not None:
                self.registry.save_account(account)
            registry_module.register_this_device(self.registry, self.cfg)
            if account is not None:
                # First device ever: its current state becomes the shared profile.
                self._engine().push(self.cfg.enabled(list(self.modules)), force=True)

        def done(_result, error):
            if error:
                self._toast(f"Setup failed: {error}")
                button.set_sensitive(True)
            else:
                self._show_main()
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)

    # -- main view: device list ---------------------------------------------

    def _show_main(self):
        page = Adw.PreferencesPage()

        account_group = Adw.PreferencesGroup(title="Account")
        self.account_row = Adw.ActionRow(title="OneDrive", subtitle="Loading…")
        account_group.add(self.account_row)
        page.add(account_group)

        self.devices_group = Adw.PreferencesGroup(
            title="Devices", description="Select a device to choose what it synchronizes.")
        page.add(self.devices_group)
        self.device_rows = []

        sync_group = Adw.PreferencesGroup(title="Synchronization")
        self.push_button = self._action_row(sync_group, "Upload this device's settings", "Upload", self._on_push)
        self.pull_button = self._action_row(sync_group, "Apply settings from the cloud", "Download", self._on_pull)
        page.add(sync_group)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.connect("clicked", lambda _b: self._reload_main())
        self._set_root(_page("Asinux Sync", page, header_end=[refresh]))
        self._reload_main(background_sync=True)

    def _action_row(self, group, title, label, handler):
        row = Adw.ActionRow(title=title)
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        button.connect("clicked", handler)
        row.add_suffix(button)
        group.add(row)
        return button

    def _reload_main(self, background_sync=False):
        def work():
            account = self.registry.load_account() or {}
            devices = self.registry.list_devices()
            mine = next((d for d in devices if d["id"] == self.cfg.device_id), None)
            if mine is not None:
                self.cfg.disabled_modules = list(mine.get("disabled_modules", []))
                config_module.save(self.cfg)
            if background_sync:
                # Bring this device up to date with the shared profile,
                # honouring what is disabled for this device.
                self._engine().pull(self.cfg.enabled(list(self.modules)))
                registry_module.register_this_device(self.registry, self.cfg)
                devices = self.registry.list_devices()
            return account, devices

        def done(result, error):
            if error:
                self._toast(f"Sync error: {error}")
                self.account_row.set_subtitle("Couldn't load")
                return GLib.SOURCE_REMOVE
            account, devices = result
            self.account_row.set_subtitle(
                account.get("pretty_name") or account.get("username") or "Connected")
            self._fill_devices(devices)
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)

    def _fill_devices(self, devices):
        for row in self.device_rows:
            self.devices_group.remove(row)
        self.device_rows = []
        for device in devices:
            parts = ["This device"] if device["id"] == self.cfg.device_id else []
            parts.append(device.get("hostname", ""))
            parts.append(f"last seen {_format_time(device.get('last_seen'))}")
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(device.get("name", "Unnamed")),
                subtitle=GLib.markup_escape_text(" · ".join(p for p in parts if p)),
                activatable=True,
            )
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _r, d=device: self._show_device(d))
            self.devices_group.add(row)
            self.device_rows.append(row)

    def _on_push(self, _button):
        self._run_sync_action(
            "Settings uploaded", lambda: self._engine().push(self.cfg.enabled(list(self.modules))))

    def _on_pull(self, _button):
        def work():
            if self._engine().pull(self.cfg.enabled(list(self.modules))) is None:
                raise ValueError("Nothing has been uploaded yet")

        self._run_sync_action("Settings applied", work)

    def _run_sync_action(self, success_message, work):
        self.push_button.set_sensitive(False)
        self.pull_button.set_sensitive(False)

        def done(_result, error):
            self.push_button.set_sensitive(True)
            self.pull_button.set_sensitive(True)
            if isinstance(error, ConflictError):
                self._toast("Another device uploaded newer settings. Download them first.")
            elif error:
                self._toast(str(error))
            else:
                self._toast(success_message)
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)

    # -- main view: one device's toggles --------------------------------------

    def _show_device(self, device):
        is_me = device["id"] == self.cfg.device_id
        page = Adw.PreferencesPage()
        if is_me:
            group = Adw.PreferencesGroup(
                title="Synchronized on this device",
                description="Turn a category off to keep this computer's own setting - it is then "
                            "neither uploaded nor overwritten by other devices.")
        else:
            group = Adw.PreferencesGroup(
                title=f"Synchronized on {device.get('name', 'this device')}",
                description="Changes are saved to your cloud and take effect the next time "
                            "that device syncs.")
        disabled = set(device.get("disabled_modules", []))
        for module_id, module in self.modules.items():
            row = Adw.SwitchRow(
                title=module.display_name or module_id,
                subtitle=module.description,
                active=module_id not in disabled,
            )
            row.connect("notify::active", self._on_module_toggled, device["id"], module_id, is_me)
            group.add(row)
        page.add(group)
        self.nav.push(_page(device.get("name", "Device"), page))

    def _on_module_toggled(self, row, _pspec, device_id, module_id, is_me):
        enabled = row.get_active()

        def work():
            return self.registry.set_device_module(device_id, module_id, enabled)

        def done(device, error):
            if error:
                self._toast(f"Couldn't save: {error}")
            elif is_me:
                self.cfg.disabled_modules = list(device.get("disabled_modules", []))
                config_module.save(self.cfg)
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)
