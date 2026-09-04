import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from pathlib import Path

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from glyph.overrides import (  # noqa: E402
    DATA_DIR,
    export_backup,
    import_backup,
    load_state,
    restore_all_to_stock,
    revert_all_icons,
)
from glyph.window import GlyphWindow  # noqa: E402

APP_ID = "dev.the0megastar.Glyph"


class GlyphApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("search", self.on_search, ["<primary>f"])
        self.create_action("refresh-help", self.on_refresh_help)
        self.create_action("revert-all", self.on_revert_all)
        self.create_action("restore-all-stock", self.on_restore_all_stock)
        self.create_action("export-overrides", self.on_export_overrides)
        self.create_action("import-overrides", self.on_import_overrides)
        self.create_action("open-data-folder", self.on_open_data_folder)
        self.create_action("shortcuts", self.on_shortcuts, ["<primary>question"])
        self.create_action("about", self.on_about)

    def do_activate(self):
        win = self.props.active_window
        if win is None:
            win = GlyphWindow(application=self)
            win.set_icon_name(APP_ID)
        win.present()

    def on_search(self, *_args):
        win = self.props.active_window
        if win and hasattr(win, "search"):
            win.search.grab_focus()


    def on_refresh_help(self, *_args):
        dialog = Adw.AlertDialog(
            heading="Refreshing icons",
            body=(
                "Glyph saves the new launcher icon immediately. GNOME Shell often "
                "keeps the old image in the app grid until you log out and log back in.\n\n"
                "Opening the app can show the new icon on the dash for that window. "
                "Unpinning, pinning, or running desktop-menu update commands does not "
                "clear the grid cache. Glyph cannot force a refresh."
            ),
        )
        dialog.add_response("ok", "OK")
        dialog.present(self.props.active_window)

    def on_revert_all(self, *_args):
        win = self.props.active_window
        state = load_state()
        count = len(state)
        if count == 0:
            if win and hasattr(win, "_toast"):
                win._toast("No custom icons to revert.")
            return

        dialog = Adw.AlertDialog(
            heading="Revert all custom icons?",
            body=(
                f"This will restore original icons for {count} application{'s' if count != 1 else ''} "
                "and remove user overrides created by Glyph."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("revert", "Revert All")
        dialog.set_response_appearance("revert", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "revert":
                n = revert_all_icons()
                if win and hasattr(win, "reload"):
                    win.reload()
                if win and hasattr(win, "_toast"):
                    win._toast(f"Restored original icons for {n} application{'s' if n != 1 else ''}. Log out to refresh grid.")

        dialog.connect("response", on_response)
        dialog.present(win)

    def on_restore_all_stock(self, *_args):
        win = self.props.active_window
        dialog = Adw.AlertDialog(
            heading="Restore all launchers to system default?",
            body=(
                "This will remove all local launcher overrides in your personal applications "
                "folder and restore every app to its original stock name and icon as provided "
                "by the package install.\n\n"
                "Standalone user applications with no system package will not be affected.\n\n"
                "Note: If you have a custom GNOME icon theme active (e.g. via GNOME Tweaks), "
                "applications will display that theme's icons rather than the original vendor graphics."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore All to Default")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "restore":
                n = restore_all_to_stock()
                if win and hasattr(win, "reload"):
                    win.reload()
                if win and hasattr(win, "_toast"):
                    win._toast(f"Restored {n} launcher{'s' if n != 1 else ''} to system default.")

        dialog.connect("response", on_response)
        dialog.present(win)

    def on_export_overrides(self, *_args):
        win = self.props.active_window
        state = load_state()
        if not state:
            if win and hasattr(win, "_toast"):
                win._toast("No custom icons to export.")
            return

        dialog = Gtk.FileDialog(title="Export Overrides Backup")
        dialog.set_initial_name("glyph-overrides-backup.tar.gz")
        dialog.save(win, None, self._on_export_save_done)

    def _on_export_save_done(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult):
        win = self.props.active_window
        try:
            file = dialog.save_finish(result)
        except Exception:
            return
        if not file:
            return
        try:
            n = export_backup(Path(file.get_path()))
            if win and hasattr(win, "_toast"):
                win._toast(f"Exported {n} custom icon{'s' if n != 1 else ''}.")
        except Exception as exc:
            if win and hasattr(win, "_toast"):
                win._toast(f"Export failed: {exc}")

    def on_import_overrides(self, *_args):
        win = self.props.active_window
        dialog = Gtk.FileDialog(title="Restore Overrides Backup")
        dialog.open(win, None, self._on_import_open_done)

    def _on_import_open_done(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult):
        win = self.props.active_window
        try:
            file = dialog.open_finish(result)
        except Exception:
            return
        if not file:
            return
        try:
            n = import_backup(Path(file.get_path()))
            if win and hasattr(win, "reload"):
                win.reload()
            if win and hasattr(win, "_toast"):
                win._toast(f"Restored {n} custom icon{'s' if n != 1 else ''}. Log out to refresh grid.")
        except Exception as exc:
            if win and hasattr(win, "_toast"):
                win._toast(f"Restore failed: {exc}")

    def on_open_data_folder(self, *_args):
        win = self.props.active_window
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(DATA_DIR)))
        launcher.launch(win, None, None)

    def on_shortcuts(self, *_args):
        win = self.props.active_window
        shortcuts = Gtk.ShortcutsWindow(transient_for=win, modal=True)
        section = Gtk.ShortcutsSection()
        section.set_visible(True)

        group_gen = Gtk.ShortcutsGroup(title="General")
        group_gen.set_visible(True)
        group_gen.append(Gtk.ShortcutsShortcut(title="Search applications", accelerator="<primary>f", visible=True))
        group_gen.append(Gtk.ShortcutsShortcut(title="Keyboard shortcuts", accelerator="<primary>question", visible=True))
        group_gen.append(Gtk.ShortcutsShortcut(title="Quit", accelerator="<primary>q", visible=True))
        section.append(group_gen)

        group_nav = Gtk.ShortcutsGroup(title="Navigation & Editing")
        group_nav.set_visible(True)
        group_nav.append(Gtk.ShortcutsShortcut(title="Back to applications", accelerator="<alt>Left", visible=True))
        group_nav.append(Gtk.ShortcutsShortcut(title="Cancel / Close dialog", accelerator="Escape", visible=True))
        section.append(group_nav)

        shortcuts.add_section(section)
        shortcuts.present()

    def on_about(self, *_args):
        dialog = Adw.AboutDialog(
            application_name="Glyph",
            application_icon=APP_ID,
            developer_name="the0megastar",
            version="0.1.0",
            comments="A modern utility to customize and restore application icons on Linux.",
            website="https://the0megastar.github.io/Glyph",
            issue_url="https://github.com/the0megastar/Glyph/issues",
            support_url="https://github.com/the0megastar/Glyph",
            copyright="© 2026 the0megastar",
            license_type=Gtk.License.GPL_3_0,
            developers=["the0megastar"],
            designers=["the0megastar"],
            release_notes_version="0.1.0",
            release_notes=(
                "<p>Initial public release:</p>"
                "<ul>"
                "<li>Browse and search installed apps</li>"
                "<li>Set custom icons from SVG or PNG</li>"
                "<li>Safe user-level icon overrides</li>"
                "<li>One-click restore to system defaults</li>"
                "<li>Direct application test-launch</li>"
                "<li>Native Libadwaita dark mode design</li>"
                "</ul>"
            ),
        )
        dialog.add_link("Sponsor on GitHub", "https://github.com/sponsors/the0megastar")
        dialog.add_link("Support on Ko-fi", "https://ko-fi.com/the0megastar")
        dialog.present(self.props.active_window)

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(argv=None):
    Adw.init()
    icon_dir = Path(__file__).resolve().parent.parent.parent / "data" / "icons"
    if icon_dir.is_dir():
        display = Gdk.Display.get_default()
        if display:
            theme = Gtk.IconTheme.get_for_display(display)
            theme.add_search_path(str(icon_dir))
    app = GlyphApplication()
    return app.run(argv if argv is not None else sys.argv)
