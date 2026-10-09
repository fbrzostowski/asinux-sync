import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw

from .window import SyncWindow

APP_ID = "com.asinux.SyncSettings"


class SyncApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.props.active_window
        if win is None:
            win = SyncWindow(self)
        win.present()


def main():
    return SyncApp().run(sys.argv)
