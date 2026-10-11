import re
import threading
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gtk

from asinux_sync import config as config_module
from asinux_sync import daemon as daemon_module
from asinux_sync import registry as registry_module
from asinux_sync import service as service_module
from asinux_sync import system_defaults
from asinux_sync import ui_cache
from asinux_sync.cloud.rclone import RcloneBackend, RcloneError, is_installed
from asinux_sync.engine import SyncEngine
from asinux_sync.modules import REGISTRY

from .search import matches

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


_RESULT_TEXT = {
    "idle": "up to date",
    "pushed": "uploaded this device's changes",
    "pulled": "applied changes from the cloud",
    "not-configured": "not set up yet",
}


def _service_summary(active, status):
    if not active:
        return "Not running"
    if not status:
        return "Running - waiting for the first sync"
    when = _format_time(status.get("last_run"))
    if status.get("result") == "error":
        return f"Last attempt {when} failed: {status.get('error', 'unknown error')}"
    return f"Last sync {when} - {_RESULT_TEXT.get(status.get('result'), status.get('result', ''))}"


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

        self.auth_process = None
        self._toggle_lock = threading.Lock()
        self.connect("close-request", self._on_close_request)

        self._start()

    def _on_close_request(self, _window):
        # Don't leave `rclone authorize` running: it keeps port 53682 bound
        # and blocks every later sign-in attempt.
        if self.auth_process is not None and self.auth_process.poll() is None:
            self.auth_process.kill()
        return False

    # -- helpers ----------------------------------------------------------

    def _toast(self, message):
        toast = Adw.Toast.new(message)
        # Messages carry raw error text (URLs with &, rclone output with <>);
        # parsed as Pango markup they would render as an empty toast.
        toast.set_use_markup(False)
        self.toast_overlay.add_toast(toast)

    def _run_async(self, work, on_done):
        def runner():
            try:
                result, error = work(), None
            except (RcloneError, service_module.ServiceError, OSError, ValueError) as exc:
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
        elif not RcloneBackend.is_configured(self.cfg.remote_name):
            self._finish_signin()
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
            result = RcloneBackend.authorize(PROVIDER, on_auth_url, on_process=self._set_auth_process)
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

    def _set_auth_process(self, proc):
        self.auth_process = proc

    def _show_auth_url(self, url):
        dialog = Adw.AlertDialog(
            heading="Sign in to OneDrive",
            body=f"If the browser did not open, open this address in a private window:\n\n{url}",
        )
        dialog.add_response("close", "Close")
        dialog.present(self)
        return GLib.SOURCE_REMOVE

    def _finish_signin(self):
        """Signed in earlier but the remote never got its drive details."""
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, width_request=32, height_request=32)
        self._set_root(_page("Asinux Sync", _status_page(
            "emblem-synchronizing-symbolic", "Finishing sign-in…", "", spinner)))

        def done(_result, error):
            if error:
                self._toast(f"Couldn't finish sign-in: {error}")
                self._show_login()
            else:
                self._start()
            return GLib.SOURCE_REMOVE

        self._run_async(lambda: RcloneBackend.repair(self.cfg.remote_name), done)

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
            registry_module.register_this_device(self.registry, self.cfg, self.modules)
            if account is not None:
                # First device ever: its current state becomes the shared profile.
                self._engine().push(self.cfg.disabled_modules, force=True)

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
        self.account_row = Adw.ActionRow(title="OneDrive", subtitle="Loading…", use_markup=False)
        account_group.add(self.account_row)
        page.add(account_group)

        self.devices_group = Adw.PreferencesGroup(
            title="Devices", description="Select a device to choose what it synchronizes.")
        page.add(self.devices_group)
        self.device_rows = []

        page.add(self._build_background_group())

        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.connect("clicked", lambda _b: self._reload_main())
        self._set_root(_page("Asinux Sync", page, header_end=[refresh]))
        # Show what we saw last time right away; fresher data replaces it
        # as soon as the cloud answers.
        self.cached = ui_cache.load()
        if self.cached:
            self._show_account_and_devices(self.cached, "updating…")
        self._reload_main()
        self._ensure_service()

    # -- main view: background service ----------------------------------------

    def _build_background_group(self):
        group = Adw.PreferencesGroup(
            title="Background sync",
            description="A background service keeps this device and your cloud up to date. "
                        "The settings below apply to this device only.")
        self._updating_service_ui = True
        self.service_row = Adw.SwitchRow(title="Sync in the background", subtitle="Checking…", use_markup=False)
        self.service_row.set_active(self.cfg.background_sync)
        self.service_row.connect("notify::active", self._on_service_toggled)
        group.add(self.service_row)

        adjustment = Gtk.Adjustment(
            value=max(1, round(self.cfg.sync_interval_seconds / 60)),
            lower=1, upper=1440, step_increment=1, page_increment=10)
        self.interval_row = Adw.SpinRow(
            title="Sync interval", subtitle="Minutes between syncs", adjustment=adjustment)
        self.interval_row.connect("notify::value", self._on_interval_changed)
        group.add(self.interval_row)
        self._updating_service_ui = False
        return group

    def _on_interval_changed(self, row, _pspec):
        if self._updating_service_ui:
            return
        # The service notices the config change on its own and re-times itself.
        self.cfg.sync_interval_seconds = max(
            config_module.MIN_INTERVAL_SECONDS, int(row.get_value()) * 60)
        config_module.save(self.cfg)

    def _on_service_toggled(self, row, _pspec):
        if self._updating_service_ui:
            return
        enable = row.get_active()
        self.cfg.background_sync = enable
        config_module.save(self.cfg)

        def done(_result, error):
            if error:
                self._toast(f"Couldn't {'start' if enable else 'stop'} the service: {error}")
                self._updating_service_ui = True
                row.set_active(not enable)
                self.cfg.background_sync = not enable
                config_module.save(self.cfg)
                self._updating_service_ui = False
            self._refresh_service_status()
            return GLib.SOURCE_REMOVE

        self._run_async(service_module.enable if enable else service_module.disable, done)

    def _ensure_service(self):
        """Install/start the service on launch if this device wants it."""
        if not self.cfg.background_sync:
            self._refresh_service_status()
            return

        def work():
            service_module.install_unit()
            if not (service_module.is_enabled() and service_module.is_active()):
                service_module.enable()

        def done(_result, error):
            if error:
                self._toast(f"Couldn't start the background service: {error}")
            self._refresh_service_status()
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)

    def _refresh_service_status(self):
        def work():
            return service_module.is_active(), daemon_module.read_status()

        def done(result, error):
            if error:
                self.service_row.set_subtitle(str(error))
                return GLib.SOURCE_REMOVE
            active, status = result
            self.service_row.set_subtitle(_service_summary(active, status))
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)

    # -- main view: devices ---------------------------------------------------

    def _show_account_and_devices(self, data, status=""):
        account = data.get("account") or {}
        name = account.get("pretty_name") or account.get("username") or "Connected"
        self.account_row.set_subtitle(f"{name} · {status}" if status else name)
        self._fill_devices(data.get("devices", []))

    def _reload_main(self):
        cached = getattr(self, "cached", None)
        if cached:
            self._show_account_and_devices(cached, "updating…")
        else:
            self.account_row.set_subtitle("Loading…")

        def work():
            account = self.registry.load_account() or {}
            devices = self.registry.list_devices()
            mine = next((d for d in devices if d["id"] == self.cfg.device_id), None)
            if mine is not None:
                self.cfg.disabled_modules = list(mine.get("disabled_modules", []))
                config_module.save(self.cfg)
            fetched = {"fetched_at": registry_module.now_iso(), "account": account, "devices": devices}
            ui_cache.save(account, devices, fetched["fetched_at"])
            return fetched

        def done(fetched, error):
            if error:
                if self.cached:
                    self._show_account_and_devices(
                        self.cached, f"offline, showing data from {_format_time(self.cached.get('fetched_at'))}")
                else:
                    self.account_row.set_subtitle("Couldn't load")
                self._toast(f"Couldn't reach your cloud: {error}")
                return GLib.SOURCE_REMOVE
            self.cached = fetched
            self._show_account_and_devices(fetched)
            self._refresh_service_status()
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)

    def _fill_devices(self, devices):
        if devices == getattr(self, "_shown_devices", None):
            return  # same list as already on screen: don't rebuild (and flicker)
        self._shown_devices = devices
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

    # -- main view: one device's toggles --------------------------------------

    def _show_device(self, device):
        is_me = device["id"] == self.cfg.device_id
        page = Adw.PreferencesPage()
        if is_me:
            intro = Adw.PreferencesGroup(
                title="Synchronized on this device",
                description="Turn a category off to keep this computer's own setting - it is then "
                            "neither uploaded nor overwritten by other devices. Expand a category "
                            "to switch off single parts of it.")
        else:
            intro = Adw.PreferencesGroup(
                title=f"Synchronized on {device.get('name', 'this device')}",
                description="Changes are saved to your cloud and take effect the next time "
                            "that device syncs.")
        page.add(intro)

        disabled = set(device.get("disabled_modules", []))
        # Another device's own list of items (its extensions...) if it has published one.
        catalog = device.get("catalog") or {}
        if is_me:
            catalog = registry_module.build_catalog(self.modules)

        groups = {}
        rows_by_group = {}
        for module_id, module in self.modules.items():
            items = catalog.get(module_id)
            if items is None:
                items = [{"id": i.id, "name": i.name, "description": i.description} for i in module.list_items()]
            if module.category not in groups:
                groups[module.category] = Adw.PreferencesGroup(title=module.category)
                rows_by_group[module.category] = []
                page.add(groups[module.category])
            row = self._module_row(device["id"], module_id, module, items, disabled, is_me)
            row.sync_text = f"{module.display_name} {module.description} {module.category}"
            groups[module.category].add(row)
            rows_by_group[module.category].append(row)

        no_results = Adw.PreferencesGroup(visible=False)
        no_results.add(Adw.ActionRow(title="Nothing matches your search", use_markup=False))
        page.add(no_results)

        def filter_rows(query):
            query = query.strip()
            any_visible = False
            for category, group in groups.items():
                group_visible = False
                for row in rows_by_group[category]:
                    children = getattr(row, "sync_children", [])
                    module_match = matches(query, row.sync_text)
                    matching = [child for child, text in children if matches(query, text)]
                    visible = not query or module_match or bool(matching)
                    row.set_visible(visible)
                    for child, _text in children:
                        child.set_visible(not query or module_match or child in matching)
                    if isinstance(row, Adw.ExpanderRow):
                        # Open the rows whose parts matched, so the hit is on screen.
                        row.set_expanded(bool(query) and bool(matching))
                    group_visible = group_visible or visible
                group.set_visible(group_visible)
                any_visible = any_visible or group_visible
            intro.set_visible(not query)
            no_results.set_visible(bool(query) and not any_visible)

        self.nav.push(self._searchable_page(device.get("name", "Device"), page, filter_rows))

    def _searchable_page(self, title, content, on_query):
        """A page with a search button (and Ctrl+F, or just start typing)."""
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toggle = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+F)")
        header.pack_end(toggle)
        entry = Gtk.SearchEntry(placeholder_text="Search what to sync", hexpand=True)
        bar = Gtk.SearchBar()
        bar.set_child(Adw.Clamp(maximum_size=420, child=entry))
        bar.connect_entry(entry)
        bar.set_key_capture_widget(view)
        toggle.bind_property(
            "active", bar, "search-mode-enabled", GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)

        def on_mode_changed(search_bar, _pspec):
            if not search_bar.get_search_mode():
                entry.set_text("")
                on_query("")

        def open_search(*_args):
            toggle.set_active(True)
            entry.grab_focus()
            return True

        bar.connect("notify::search-mode-enabled", on_mode_changed)
        entry.connect("search-changed", lambda e: on_query(e.get_text()))
        shortcuts = Gtk.ShortcutController()
        shortcuts.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>f"), Gtk.CallbackAction.new(open_search)))
        view.add_controller(shortcuts)

        view.add_top_bar(header)
        view.add_top_bar(bar)
        view.set_content(content)
        return Adw.NavigationPage(title=title, child=view)

    def _module_row(self, device_id, module_id, module, items, disabled, is_me):
        items = [item for item in items if not item.get("hidden")]
        if len(items) <= 1 and not any(item.get("modes") for item in items):
            row = Adw.SwitchRow(
                title=module.display_name, subtitle=module.description,
                active=module_id not in disabled, use_markup=False)
            row.connect("notify::active", self._on_module_toggled, device_id, module_id, is_me)
            return row

        row = Adw.ExpanderRow(
            title=module.display_name, subtitle=module.description, use_markup=False,
            show_enable_switch=True, enable_expansion=module_id not in disabled)
        row.sync_children = []
        row.connect("notify::enable-expansion", self._on_module_toggled, device_id, module_id, is_me)
        for item in items:
            if item.get("modes"):
                item_row = self._mode_row(device_id, module_id, item, disabled, is_me)
            else:
                key = f"{module_id}.{item['id']}"
                item_row = Adw.SwitchRow(
                    title=item["name"], subtitle=item.get("description", ""),
                    active=key not in disabled, use_markup=False)
                item_row.connect("notify::active", self._on_module_toggled, device_id, key, is_me)
            row.add_row(item_row)
            row.sync_children.append((item_row, f"{item['name']} {item.get('description', '')}"))
        return row

    def _mode_row(self, device_id, module_id, item, disabled, is_me):
        """A menu instead of a switch: each choice switches off a set of items."""
        modes = item["modes"]
        union = {off for mode in modes for off in mode["off"]}
        off_now = {off for off in union if f"{module_id}.{off}" in disabled}
        # The choice matching what is off now; failing that the strongest one contained in it.
        selected = next((i for i, m in enumerate(modes) if set(m["off"]) == off_now), None)
        if selected is None:
            fitting = [i for i, m in enumerate(modes) if set(m["off"]) <= off_now]
            selected = max(fitting, key=lambda i: len(modes[i]["off"]), default=0)

        row = Adw.ComboRow(title=item["name"], subtitle=item.get("description", ""), use_markup=False)
        row.set_model(Gtk.StringList.new([mode["label"] for mode in modes]))
        row.set_selected(selected)
        row.connect("notify::selected", self._on_mode_selected, device_id, module_id, modes, is_me)
        return row

    def _on_mode_selected(self, row, _pspec, device_id, module_id, modes, is_me):
        chosen = set(modes[row.get_selected()]["off"])
        changes = {f"{module_id}.{off}": off not in chosen for mode in modes for off in mode["off"]}
        self._save_switches(device_id, changes, is_me)

    def _on_module_toggled(self, row, _pspec, device_id, key, is_me):
        # `key` is "<module>" for a whole category, "<module>.<item>" for one part.
        enabled = row.get_enable_expansion() if isinstance(row, Adw.ExpanderRow) else row.get_active()
        self._save_switches(device_id, {key: enabled}, is_me)

    def _save_switches(self, device_id, changes, is_me):
        def work():
            # Read-modify-write of one cloud file: quick successive clicks must queue up.
            with self._toggle_lock:
                return self.registry.set_device_keys(device_id, changes)

        def done(device, error):
            if error:
                self._toast(f"Couldn't save: {error}")
            elif is_me:
                self.cfg.disabled_modules = list(device.get("disabled_modules", []))
                config_module.save(self.cfg)
            return GLib.SOURCE_REMOVE

        self._run_async(work, done)
