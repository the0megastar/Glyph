import os
import sys
from pathlib import Path

from glyph.paths import configure_xdg_data_dirs

# Configure GLib/GTK before importing it, so icon lookup uses the same host and
# package-export roots as desktop-file discovery.
configure_xdg_data_dirs()

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GioUnix", "2.0")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from glyph import __version__, dialogs  # noqa: E402
from glyph.overrides import DATA_DIR, OverrideError  # noqa: E402
from glyph.window import GlyphWindow  # noqa: E402

APP_ID = "io.github.the0megastar.Glyph"

SHORTCUTS_UI = """<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="shortcuts">
    <property name="modal">True</property>
    <child>
      <object class="GtkShortcutsSection">
        <property name="visible">True</property>
        <property name="section-name">shortcuts</property>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="visible">True</property>
            <property name="title">General</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="visible">True</property>
                <property name="title">Search applications</property>
                <property name="accelerator">&lt;primary&gt;f</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="visible">True</property>
                <property name="title">Preferences</property>
                <property name="accelerator">&lt;primary&gt;comma</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="visible">True</property>
                <property name="title">Keyboard shortcuts</property>
                <property name="accelerator">&lt;primary&gt;question</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="visible">True</property>
                <property name="title">Quit</property>
                <property name="accelerator">&lt;primary&gt;q</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="visible">True</property>
            <property name="title">Navigation &amp; Editing</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="visible">True</property>
                <property name="title">Back to applications</property>
                <property name="accelerator">&lt;alt&gt;Left</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="visible">True</property>
                <property name="title">Cancel / Close dialog</property>
                <property name="accelerator">Escape</property>
              </object>
            </child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>
"""


class GlyphApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.settings = Gio.Settings.new(APP_ID)
        self.settings.connect("changed::color-scheme", self._on_color_scheme_changed)
        self._apply_color_scheme(self.settings.get_string("color-scheme"))
        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("search", self.on_search, ["<primary>f"])
        self.create_action("refresh-help", self.on_refresh_help)
        self.create_action("revert-all", self.on_revert_all)
        self.create_action("revert-all-names", self.on_revert_all_names)
        self.create_action("restore-all-stock", self.on_restore_all_stock)
        self.create_action("export-overrides", self.on_export_overrides)
        self.create_action("import-overrides", self.on_import_overrides)
        self.create_action("open-data-folder", self.on_open_data_folder)
        self.create_action("preferences", self.on_preferences, ["<primary>comma"])
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

    def _apply_color_scheme(self, value: str) -> None:
        schemes = {
            "default": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }
        Adw.StyleManager.get_default().set_color_scheme(schemes.get(value, Adw.ColorScheme.DEFAULT))

    def _on_color_scheme_changed(self, settings: Gio.Settings, _key: str) -> None:
        self._apply_color_scheme(settings.get_string("color-scheme"))


    def on_refresh_help(self, *_args):
        dialogs.show_refresh_help(self.props.active_window)

    def on_revert_all(self, *_args):
        dialogs.confirm_revert_all_icons(self.props.active_window)

    def on_revert_all_names(self, *_args):
        dialogs.confirm_revert_all_names(self.props.active_window)

    def on_restore_all_stock(self, *_args):
        dialogs.confirm_restore_all_stock(self.props.active_window)

    def on_export_overrides(self, *_args):
        dialogs.export_overrides_dialog(self.props.active_window)

    def on_import_overrides(self, *_args):
        dialogs.import_overrides_dialog(self.props.active_window)

    def _on_import_open_done(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult):
        dialogs.handle_import_open_done(self.props.active_window, dialog, result)


    def on_open_data_folder(self, *_args):
        win = self.props.active_window
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(DATA_DIR)))
        launcher.launch(win, None, None)

    def on_preferences(self, *_args):
        dialog = Adw.PreferencesDialog()
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Appearance")
        page.add(group)
        dialog.add(page)

        selected = self.settings.get_string("color-scheme")
        first_button = None
        for title, value in (
            ("Follow System", "default"),
            ("Light", "light"),
            ("Dark", "dark"),
        ):
            row = Adw.ActionRow(title=title)
            button = Gtk.CheckButton()
            if first_button is None:
                first_button = button
            else:
                button.set_group(first_button)
            button.set_active(value == selected)
            button.connect(
                "toggled",
                lambda active_button, setting=value: active_button.get_active()
                and self.settings.set_string("color-scheme", setting),
            )
            row.add_suffix(button)
            row.set_activatable_widget(button)
            group.add(row)

        dialog.present(self.props.active_window)



    def on_shortcuts(self, *_args):
        win = self.props.active_window
        builder = Gtk.Builder.new_from_string(SHORTCUTS_UI, -1)
        shortcuts = builder.get_object("shortcuts")
        shortcuts.set_transient_for(win)
        shortcuts.present()

    def on_about(self, *_args):
        dialog = Adw.AboutDialog(
            application_name="Glyph",
            application_icon=APP_ID,
            developer_name="the0megastar",
            version=__version__,
            comments="A modern utility to customize and restore application icons on Linux.",
            website="https://the0megastar.github.io/Glyph",
            issue_url="https://github.com/the0megastar/Glyph/issues",
            support_url="https://github.com/the0megastar/Glyph",
            copyright="© 2026 the0megastar",
            license_type=Gtk.License.GPL_3_0,
            developers=["the0megastar"],
            release_notes_version=__version__,
            release_notes=(
                "<p>Transactional safety, portable v2 backups, and desktop ID compliance:</p>"
                "<ul>"
                "<li>Introduced write-ahead transaction logging with crash recovery</li>"
                "<li>Portable v2 backup format with conflict detection and custom name support</li>"
                "<li>Safe bulk revert operations preserving custom names and pre-existing launchers</li>"
                "<li>Full support for nested desktop IDs and localized Name[locale] preservation</li>"
                "<li>Hardened native packaging dependencies requiring GTK 4.10 and Libadwaita 1.5</li>"
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
    display = Gdk.Display.get_default()
    if display:
        theme = Gtk.IconTheme.get_for_display(display)
        icon_dir = Path(__file__).resolve().parent.parent.parent / "data" / "icons"
        if icon_dir.is_dir():
            theme.add_search_path(str(icon_dir))
        if Path("/.flatpak-info").exists():
            for host_icon_dir in [
                "/run/host/usr/share/icons",
                "/run/host/usr/share/pixmaps",
                "/var/lib/flatpak/exports/share/icons",
            ]:
                if Path(host_icon_dir).is_dir():
                    theme.add_search_path(host_icon_dir)
    app = GlyphApplication()
    return app.run(argv if argv is not None else sys.argv)
