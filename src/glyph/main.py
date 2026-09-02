import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from glyph.window import GlyphWindow  # noqa: E402

APP_ID = "dev.the0megastar.Glyph"


class GlyphApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("about", self.on_about)

    def do_activate(self):
        win = self.props.active_window
        if win is None:
            win = GlyphWindow(application=self)
        win.present()

    def on_about(self, *_args):
        dialog = Adw.AboutDialog(
            application_name="Glyph",
            application_icon=APP_ID,
            developer_name="the0megastar",
            version="0.1.0",
            comments="Change and restore application icons using user-level desktop entries.",
            developers=["the0megastar"],
        )
        dialog.present(self.props.active_window)

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(argv=None):
    Adw.init()
    app = GlyphApplication()
    return app.run(argv if argv is not None else sys.argv)
