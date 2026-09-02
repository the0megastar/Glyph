from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from glyph.catalog import AppEntry, list_apps
from glyph.overrides import OverrideError, apply_icon, load_state, revert_icon


def _image_from_gicon(gicon: Gio.Icon | None, pixel_size: int) -> Gtk.Image:
    image = Gtk.Image()
    image.set_pixel_size(pixel_size)
    if gicon is not None:
        image.set_from_gicon(gicon)
    else:
        image.set_from_icon_name("application-x-executable")
    return image


def _primary_menu_button() -> Gtk.MenuButton:
    menu = Gio.Menu()
    help_section = Gio.Menu()
    help_section.append("How to refresh icons", "app.refresh-help")
    menu.append_section(None, help_section)
    about_section = Gio.Menu()
    about_section.append("About Glyph", "app.about")
    menu.append_section(None, about_section)
    return Gtk.MenuButton(
        icon_name="open-menu-symbolic",
        tooltip_text="Main menu",
        menu_model=menu,
        primary=True,
    )


class GlyphWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Glyph")
        self.set_default_size(560, 680)

        self._apps: list[AppEntry] = []
        self._query = ""
        self._rows: dict[str, Adw.ActionRow] = {}
        self._detail_id: str | None = None

        self.toast_overlay = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toast_overlay.set_child(self.nav)
        self.set_content(self.toast_overlay)

        self.nav.add(self._build_list_page())
        self.nav.connect("popped", self._on_popped)
        self.connect("map", lambda *_: self.reload())

    def _on_popped(self, _nav: Adw.NavigationView, page: Adw.NavigationPage) -> None:
        if page == getattr(self, "_detail_page", None):
            self._detail_id = None

    def _build_list_page(self) -> Adw.NavigationPage:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.search = Gtk.SearchEntry(placeholder_text="Search applications")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._on_search)
        header.set_title_widget(self.search)

        header.pack_end(_primary_menu_button())
        toolbar.add_top_bar(header)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-activated", self._on_row_activated)

        clamp = Adw.Clamp(maximum_size=640)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        clamp.set_child(self.listbox)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)

        return Adw.NavigationPage(title="Glyph", child=toolbar)

    def reload(self) -> None:
        self._apps = list_apps(load_state())
        self._rebuild_list()
        if self._detail_id and getattr(self, "_detail_page", None) is not None:
            app = self._find(self._detail_id)
            if app is not None:
                self._fill_detail(app)

    def _find(self, desktop_id: str) -> AppEntry | None:
        for app in self._apps:
            if app.desktop_id == desktop_id:
                return app
        return None

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._query = entry.get_text().strip().casefold()
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        self._rows.clear()

        query = self._query
        visible = 0
        for app in self._apps:
            if query and query not in app.name.casefold() and query not in app.desktop_id.casefold():
                continue
            row = self._make_row(app)
            self.listbox.append(row)
            self._rows[app.desktop_id] = row
            visible += 1

        if visible == 0:
            empty = Adw.ActionRow()
            empty.set_title("No applications found")
            empty.set_subtitle("Try a different search.")
            empty.set_sensitive(False)
            self.listbox.append(empty)

    def _make_row(self, app: AppEntry) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(app.name)
        row.set_subtitle(app.source)
        row.set_activatable(True)
        row.add_prefix(_image_from_gicon(app.gicon, 32))
        if app.custom:
            badge = Gtk.Label(label="Custom")
            badge.add_css_class("accent")
            badge.add_css_class("caption")
            row.add_suffix(badge)
        row.desktop_id = app.desktop_id
        return row

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        desktop_id = getattr(row, "desktop_id", None)
        if not desktop_id:
            return
        app = self._find(desktop_id)
        if app is None:
            return
        self._detail_id = desktop_id
        page = self._build_detail_page(app)
        self.nav.push(page)

    def _build_detail_page(self, app: AppEntry) -> Adw.NavigationPage:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.pack_end(_primary_menu_button())
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(18)
        box.set_margin_end(18)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        hero.set_halign(Gtk.Align.CENTER)

        self._icon_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._icon_host.set_halign(Gtk.Align.CENTER)
        self._detail_icon = _image_from_gicon(app.gicon, 96)
        self._icon_host.append(self._detail_icon)
        hero.append(self._icon_host)

        self._detail_title = Gtk.Label(label=app.name)
        self._detail_title.add_css_class("title-1")
        self._detail_title.set_wrap(True)
        self._detail_title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        hero.append(self._detail_title)

        self._detail_source = Gtk.Label()
        self._detail_source.add_css_class("dim-label")
        hero.append(self._detail_source)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons.set_halign(Gtk.Align.CENTER)

        change = Gtk.Button(label="Change icon")
        change.add_css_class("suggested-action")
        change.add_css_class("pill")
        change.connect("clicked", self._on_change)
        buttons.append(change)

        self._revert_button = Gtk.Button(label="Revert")
        self._revert_button.add_css_class("destructive-action")
        self._revert_button.add_css_class("pill")
        self._revert_button.connect("clicked", self._on_revert)
        buttons.append(self._revert_button)
        hero.append(buttons)
        box.append(hero)

        group = Adw.PreferencesGroup()
        self._desktop_file_row = Adw.ActionRow(title="Desktop file")
        self._desktop_file_row.set_subtitle_lines(2)
        self._open_desktop_file = Gtk.Button(icon_name="folder-open-symbolic")
        self._open_desktop_file.add_css_class("flat")
        self._open_desktop_file.set_valign(Gtk.Align.CENTER)
        self._open_desktop_file.set_tooltip_text("Open in Files")
        self._open_desktop_file.connect("clicked", self._on_open_desktop_file)
        self._desktop_file_row.add_suffix(self._open_desktop_file)
        group.add(self._desktop_file_row)

        self._app_folder_row = Adw.ActionRow(title="App folder")
        self._app_folder_row.set_subtitle_lines(2)
        self._open_app_folder = Gtk.Button(icon_name="folder-open-symbolic")
        self._open_app_folder.add_css_class("flat")
        self._open_app_folder.set_valign(Gtk.Align.CENTER)
        self._open_app_folder.set_tooltip_text("Open in Files")
        self._open_app_folder.connect("clicked", self._on_open_app_folder)
        self._app_folder_row.add_suffix(self._open_app_folder)
        group.add(self._app_folder_row)

        self._command_row = Adw.ActionRow(title="Command")
        self._command_row.set_subtitle_lines(3)
        group.add(self._command_row)
        box.append(group)

        clamp = Adw.Clamp(maximum_size=560)
        clamp.set_child(box)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)

        self._detail_page = Adw.NavigationPage(title=app.name, child=toolbar)
        self._fill_detail(app)
        return self._detail_page

    def _fill_detail(self, app: AppEntry) -> None:
        if hasattr(self, "_icon_host"):
            self._icon_host.remove(self._detail_icon)
            self._detail_icon = _image_from_gicon(app.gicon, 96)
            self._icon_host.append(self._detail_icon)
        self._detail_title.set_label(app.name)
        subtitle = app.source
        if app.custom:
            subtitle += " · custom icon"
        self._detail_source.set_label(subtitle)
        self._revert_button.set_sensitive(app.custom)
        self._desktop_file_row.set_subtitle(app.filename or "No desktop file")
        self._open_desktop_file.set_sensitive(bool(app.filename))
        self._app_folder_row.set_subtitle(app.app_folder or "Unknown")
        self._open_app_folder.set_sensitive(bool(app.app_folder))
        self._command_row.set_subtitle(app.command or "No command")

    def _on_change(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        dialog = Gtk.FileDialog(title="Choose an icon")
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Images")
        for mime in ("image/png", "image/svg+xml", "image/jpeg", "image/webp"):
            image_filter.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(image_filter)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_icon_chosen)

    def _on_icon_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.GError:
            return
        if file is None or not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return
        try:
            apply_icon(app.desktop_id, app.filename, file.get_path())
        except OverrideError as exc:
            self._toast(str(exc))
            return
        self.reload()
        self._toast("Icon saved. Log out to refresh the app grid.")

    def _on_revert(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        try:
            revert_icon(self._detail_id)
        except OverrideError as exc:
            self._toast(str(exc))
            return
        self.reload()
        self._toast("Original icon restored. Log out to refresh the app grid.")

    def _on_open_desktop_file(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None or not app.filename:
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(app.filename))
        launcher.open_containing_folder(self, None, self._on_open_file_done)

    def _on_open_app_folder(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None or not app.app_folder:
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(app.app_folder))
        launcher.open_containing_folder(self, None, self._on_open_file_done)

    def _on_open_file_done(self, launcher: Gtk.FileLauncher, result: Gio.AsyncResult) -> None:
        try:
            launcher.open_containing_folder_finish(result)
        except GLib.GError:
            self._toast("Could not open Files.")

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message))

