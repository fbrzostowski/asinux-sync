"""Asks whether to install GNOME Shell extensions this computer is missing.

Run by the background sync service (which has no window of its own):

    python3 -m asinux_sync_settings.extension_prompt REQUEST.json RESULT.json

REQUEST.json: {"extensions": [{"uuid": ..., "name": ...}, ...]}
RESULT.json is written only when the user decides (see
missing_extensions.build_result). Closing the window without choosing writes
nothing, which the service reads as "ask again later".
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from asinux_sync import missing_extensions

APP_ID = "com.asinux.SyncExtensions"


def write_result(path: Path, result: dict) -> None:
    temp = Path(str(path) + ".tmp")
    temp.write_text(json.dumps(result))
    temp.replace(path)


class PromptWindow(Adw.ApplicationWindow):
    def __init__(self, app, requested: list[dict], result_path: Path, installer=missing_extensions.install_remote):
        super().__init__(application=app)
        self.requested = requested
        self.result_path = Path(result_path)
        self.installer = installer
        self.busy = False
        self.set_title("Asinux Sync")
        self.set_default_size(460, -1)
        self.connect("close-request", lambda _w: self.busy)  # can't be closed mid-install

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Extensions missing on this computer",
            description="These GNOME Shell extensions are turned on on your other computers but are not "
                        "installed here. GNOME Shell will ask you to confirm each one it installs.")
        self.rows = {}
        for entry in requested:
            row = Adw.SwitchRow(title=entry["name"], subtitle=entry["uuid"], active=True, use_markup=False)
            self.rows[entry["uuid"]] = row
            group.add(row)
        page.add(group)

        actions = Adw.PreferencesGroup(
            description="“No thanks” means this computer won't ask about these extensions again.")
        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.decline_button = Gtk.Button(label="No thanks")
        self.install_button = Gtk.Button(label="Install selected")
        self.install_button.add_css_class("suggested-action")
        self.decline_button.connect("clicked", self._on_decline)
        self.install_button.connect("clicked", self._on_install)
        box.append(self.decline_button)
        box.append(self.install_button)
        actions.add(box)
        self.status = Gtk.Label(wrap=True, visible=False)
        actions.add(self.status)
        page.add(actions)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        self.set_content(view)

    def _selected(self) -> set[str]:
        return {uuid for uuid, row in self.rows.items() if row.get_active()}

    def _finish(self, result: dict) -> None:
        write_result(self.result_path, result)
        self.busy = False
        self.close()

    def _on_decline(self, _button) -> None:
        self._finish(missing_extensions.build_result([e["uuid"] for e in self.requested], set(), {}))

    def _on_install(self, _button) -> None:
        selected = self._selected()
        requested = [e["uuid"] for e in self.requested]
        if not selected:
            self._on_decline(None)
            return
        self.busy = True
        for widget in (self.install_button, self.decline_button, *self.rows.values()):
            widget.set_sensitive(False)
        self.status.set_visible(True)
        names = {e["uuid"]: e["name"] for e in self.requested}

        def work():
            outcomes = {}
            for uuid in requested:
                if uuid in selected:
                    GLib.idle_add(self.status.set_text, f"Installing {names[uuid]}… (confirm in GNOME Shell if asked)")
                    outcomes[uuid] = self.installer(uuid)
            GLib.idle_add(self._finish, missing_extensions.build_result(requested, selected, outcomes))

        threading.Thread(target=work, daemon=True).start()


class PromptApp(Adw.Application):
    def __init__(self, requested: list[dict], result_path: Path):
        super().__init__(application_id=APP_ID)
        self.requested = requested
        self.result_path = result_path

    def do_activate(self):
        PromptWindow(self, self.requested, self.result_path).present()


def main(argv: list[str]) -> int:
    request_path, result_path = Path(argv[1]), Path(argv[2])
    requested = json.loads(request_path.read_text())["extensions"]
    return PromptApp(requested, result_path).run([])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
