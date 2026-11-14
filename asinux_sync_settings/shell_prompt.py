"""Asks whether to set up the shell like on the user's other computers.

Run by the background sync service (which has no window of its own):

    python3 -m asinux_sync_settings.shell_prompt REQUEST.json RESULT.json

REQUEST.json: {"steps": [...]} (see asinux_sync/shell_setup.py).
RESULT.json is written only when the user decides (shell_setup.build_result).
Closing the window without choosing writes nothing: "ask again later".
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

from asinux_sync import shell_setup

from .extension_prompt import write_result

APP_ID = "com.asinux.SyncShell"


class PromptWindow(Adw.ApplicationWindow):
    def __init__(self, app, steps: list[dict], result_path: Path, runner=shell_setup.run):
        super().__init__(application=app)
        self.steps = steps
        self.result_path = Path(result_path)
        self.runner = runner
        self.busy = False
        self.set_title("Asinux Sync")
        self.set_default_size(460, -1)
        self.connect("close-request", lambda _w: self.busy)  # can't be closed mid-install

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Shell setup from your other computers",
            description="Your other computers use this shell setup. Installing packages or changing "
                        "the default shell asks for the administrator password once.")
        self.rows = {}
        for step in steps:
            row = Adw.SwitchRow(title=step["title"], subtitle=step.get("subtitle", ""), active=True, use_markup=False)
            self.rows[step["id"]] = row
            group.add(row)
        page.add(group)

        actions = Adw.PreferencesGroup(description="“No thanks” means this computer won't ask about these again.")
        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.decline_button = Gtk.Button(label="No thanks")
        self.apply_button = Gtk.Button(label="Set up selected")
        self.apply_button.add_css_class("suggested-action")
        self.decline_button.connect("clicked", self._on_decline)
        self.apply_button.connect("clicked", self._on_apply)
        box.append(self.decline_button)
        box.append(self.apply_button)
        actions.add(box)
        self.status = Gtk.Label(wrap=True, visible=False)
        actions.add(self.status)
        page.add(actions)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        self.set_content(view)

    def _finish(self, result: dict) -> None:
        write_result(self.result_path, result)
        self.busy = False
        self.close()

    def _on_decline(self, _button) -> None:
        self._finish(shell_setup.build_result([s["id"] for s in self.steps], set(), {}))

    def _on_apply(self, _button) -> None:
        selected = {step_id for step_id, row in self.rows.items() if row.get_active()}
        requested = [s["id"] for s in self.steps]
        if not selected:
            self._on_decline(None)
            return
        self.busy = True
        for widget in (self.apply_button, self.decline_button, *self.rows.values()):
            widget.set_sensitive(False)
        self.status.set_visible(True)
        self.status.set_text("Setting up… (enter your password if asked)")
        chosen = [s for s in self.steps if s["id"] in selected]

        def work():
            outcomes = self.runner(chosen, GLib.get_user_name())
            GLib.idle_add(self._finish, shell_setup.build_result(requested, selected, outcomes))

        threading.Thread(target=work, daemon=True).start()


class PromptApp(Adw.Application):
    def __init__(self, steps: list[dict], result_path: Path):
        super().__init__(application_id=APP_ID)
        self.steps = steps
        self.result_path = result_path

    def do_activate(self):
        PromptWindow(self, self.steps, self.result_path).present()


def main(argv: list[str]) -> int:
    request_path, result_path = Path(argv[1]), Path(argv[2])
    steps = json.loads(request_path.read_text())["steps"]
    return PromptApp(steps, result_path).run([])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
