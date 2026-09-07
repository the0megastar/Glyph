from pathlib import Path
import subprocess

import gi

gi.require_version("GioUnix", "2.0")

from gi.repository import Adw, Gdk, Gio, GioUnix, GLib, GObject, Gtk, Pango

from glyph.catalog import AppEntry, list_apps
from glyph.paths import in_flatpak
from glyph.overrides import (
    OverrideError,
    apply_icon,
    apply_name,
    load_state,
    restore_stock_launcher,
    revert_icon,
    revert_name,
)


def _image_from_gicon(gicon: Gio.Icon | None, pixel_size: int) -> Gtk.Image:
    image = Gtk.Image()
    image.set_pixel_size(pixel_size)
    if gicon is not None:
        image.set_from_gicon(gicon)
    else:
        image.set_from_icon_name("application-x-executable")
    return image


def _primary_menu_model() -> Gio.Menu:
    menu = Gio.Menu()

    overrides_section = Gio.Menu()
    overrides_section.append("Export Overrides…", "app.export-overrides")
    overrides_section.append("Restore Overrides…", "app.import-overrides")
    overrides_section.append("Open Data Folder in Files", "app.open-data-folder")
    menu.append_section(None, overrides_section)

    reset_menu = Gio.Menu()
    reset_menu.append("Revert All Custom Icons…", "app.revert-all")
    reset_menu.append("Revert All Custom Names…", "app.revert-all-names")
    reset_menu.append("Restore All to System Defaults…", "app.restore-all-stock")
    reset_section = Gio.Menu()
    reset_section.append_submenu("Reset Overrides", reset_menu)
    menu.append_section(None, reset_section)

    help_section = Gio.Menu()
    help_section.append("How to Refresh Icons…", "app.refresh-help")
    menu.append_section(None, help_section)

    about_section = Gio.Menu()
    about_section.append("Preferences", "app.preferences")
    about_section.append("Keyboard Shortcuts", "app.shortcuts")
    about_section.append("About Glyph", "app.about")
    menu.append_section(None, about_section)

    return menu


def _primary_menu_button() -> Gtk.MenuButton:
    return Gtk.MenuButton(
        icon_name="open-menu-symbolic",
        tooltip_text="Main menu",
        menu_model=_primary_menu_model(),
        primary=True,
    )



class GlyphWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Glyph")
        self.set_default_size(560, 680)

        self._apps: list[AppEntry] = []
        self._query = ""
        self._filter_custom = False
        self._rows: dict[str, Adw.ActionRow] = {}
        self._detail_id: str | None = None

        self._filter_action = Gio.SimpleAction.new_stateful(
            "filter",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string("all"),
        )
        self._filter_action.connect("change-state", self._on_filter_changed)
        self.add_action(self._filter_action)

        self._filter_menu = Gio.Menu()
        self._filter_button = Gtk.MenuButton(
            icon_name="view-filter-symbolic",
            tooltip_text="Filter applications",
            menu_model=self._filter_menu,
        )

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
        header.pack_start(self._filter_button)
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

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(scrolled, "list")

        self._empty_page = Adw.StatusPage()
        self._empty_page.set_icon_name("system-search-symbolic")
        self._empty_page.set_title("No Applications Found")
        self._empty_page.set_description("Try a different search term or clear the filter.")
        self._stack.add_named(self._empty_page, "empty")

        toolbar.set_content(self._stack)

        return Adw.NavigationPage(title="Glyph", child=toolbar)

    def reload(self) -> None:
        try:
            state = load_state()
        except OverrideError as exc:
            if hasattr(self, "_toast"):
                self._toast(str(exc))
            return
        self._apps = list_apps(state)
        self._update_filter_menu()
        self._rebuild_list()
        if self._detail_id and getattr(self, "_detail_page", None) is not None:
            app = self._find(self._detail_id)
            if app is not None:
                self._detail_page.set_title(app.name)
                self._fill_detail(app)

    def _update_filter_menu(self) -> None:
        total = len(self._apps)
        custom = sum(1 for a in self._apps if a.custom)
        self._filter_menu.remove_all()
        self._filter_menu.append(f"All applications ({total})", "win.filter::all")
        self._filter_menu.append(f"Customized only ({custom})", "win.filter::custom")

    def _find(self, desktop_id: str) -> AppEntry | None:
        for app in self._apps:
            if app.desktop_id == desktop_id:
                return app
        return None

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._query = entry.get_text().strip().casefold()
        self._rebuild_list()

    def _on_filter_changed(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self._filter_custom = (value.get_string() == "custom")
        if self._filter_custom:
            self._filter_button.add_css_class("accent")
        else:
            self._filter_button.remove_css_class("accent")
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        self._rows.clear()

        query = self._query
        custom_only = self._filter_custom
        visible = 0
        for app in self._apps:
            if custom_only and not app.custom:
                continue
            if query and query not in app.name.casefold() and query not in app.desktop_id.casefold():
                continue
            row = self._make_row(app)
            self.listbox.append(row)
            self._rows[app.desktop_id] = row
            visible += 1

        if visible == 0:
            if custom_only and not query:
                self._empty_page.set_icon_name("starred-symbolic")
                self._empty_page.set_title("No Customized Applications")
                self._empty_page.set_description("You haven't customized any app icons or names yet.")
            else:
                self._empty_page.set_icon_name("system-search-symbolic")
                self._empty_page.set_title("No Applications Found")
                self._empty_page.set_description("Try a different search term or clear the filter.")
            self._stack.set_visible_child_name("empty")
        else:
            self._stack.set_visible_child_name("list")

    def _make_row(self, app: AppEntry) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(app.name)
        subtitle = app.source
        is_shadowed = (
            app.has_stock
            and bool(app.stock_filename)
            and bool(app.filename)
            and Path(app.filename).resolve() != Path(app.stock_filename).resolve()
        )
        if app.custom:
            badge = Gtk.Label(label="Custom")
            badge.add_css_class("accent")
            badge.add_css_class("caption")
            row.add_suffix(badge)
        elif is_shadowed:
            subtitle += " · Override"
        row.set_subtitle(subtitle)
        row.set_activatable(True)
        row.add_prefix(_image_from_gicon(app.gicon, 32))
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
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(18)
        box.set_margin_end(18)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero.set_halign(Gtk.Align.CENTER)

        self._icon_host = Gtk.Overlay()
        self._icon_host.set_halign(Gtk.Align.CENTER)

        self._icon_button = Gtk.Button()
        self._icon_button.add_css_class("flat")
        self._icon_button.connect("clicked", self._on_change)
        self._icon_button.set_tooltip_text("Click or drop an image to change icon")

        self._detail_icon = _image_from_gicon(app.gicon, 96)
        self._icon_button.set_child(self._detail_icon)
        self._icon_host.set_child(self._icon_button)

        edit_icon = Gtk.Image.new_from_icon_name("document-edit-symbolic")
        edit_icon.set_pixel_size(14)
        badge_btn = Gtk.Button()
        badge_btn.set_child(edit_icon)
        badge_btn.add_css_class("circular")
        badge_btn.add_css_class("raised")
        badge_btn.set_halign(Gtk.Align.END)
        badge_btn.set_valign(Gtk.Align.END)
        badge_btn.set_tooltip_text("Change icon")
        badge_btn.connect("clicked", self._on_change)
        self._icon_host.add_overlay(badge_btn)

        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_icon_dropped)
        self._icon_host.add_controller(drop_target)
        hero.append(self._icon_host)

        icon_hint = Gtk.Label(label="Click or drop image · PNG, SVG, WebP, JPG")
        icon_hint.add_css_class("caption")
        icon_hint.add_css_class("dim-label")
        hero.append(icon_hint)

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_box.set_halign(Gtk.Align.CENTER)
        title_box.set_margin_top(4)

        self._detail_title = Gtk.Label(label=app.name)
        self._detail_title.add_css_class("title-1")
        self._detail_title.set_wrap(True)
        self._detail_title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title_box.append(self._detail_title)

        self._name_edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        self._name_edit_btn.add_css_class("flat")
        self._name_edit_btn.add_css_class("circular")
        self._name_edit_btn.set_valign(Gtk.Align.CENTER)
        self._name_edit_btn.set_tooltip_text("Rename application")
        self._name_edit_btn.connect("clicked", self._on_edit_name_clicked)
        title_box.append(self._name_edit_btn)

        self._name_reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self._name_reset_btn.add_css_class("flat")
        self._name_reset_btn.add_css_class("circular")
        self._name_reset_btn.set_valign(Gtk.Align.CENTER)
        self._name_reset_btn.set_tooltip_text("Reset to original name")
        self._name_reset_btn.set_visible(False)
        self._name_reset_btn.connect("clicked", self._on_name_reset)
        title_box.append(self._name_reset_btn)

        hero.append(title_box)

        self._badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._badge_box.set_halign(Gtk.Align.CENTER)
        hero.append(self._badge_box)

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
        self._command_row.set_activatable(True)
        self._command_row.connect("activated", self._on_launch)

        self._launch_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self._launch_btn.add_css_class("flat")
        self._launch_btn.set_valign(Gtk.Align.CENTER)
        self._launch_btn.set_tooltip_text("Launch application")
        self._launch_btn.connect("clicked", self._on_launch)
        self._command_row.add_suffix(self._launch_btn)
        group.add(self._command_row)
        box.append(group)

        self._bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._bottom_box.set_halign(Gtk.Align.CENTER)
        self._bottom_box.set_margin_top(6)

        self._revert_button = Gtk.Button(label="Revert to original icon")
        self._revert_button.add_css_class("destructive-action")
        self._revert_button.add_css_class("pill")
        self._revert_button.connect("clicked", self._on_revert)
        self._bottom_box.append(self._revert_button)

        self._stock_button = Gtk.Button(label="Restore system default launcher")
        self._stock_button.add_css_class("pill")
        self._stock_button.set_tooltip_text("Restore the original stock name and icon provided by the package install")
        self._stock_button.connect("clicked", self._on_restore_stock)
        self._bottom_box.append(self._stock_button)

        box.append(self._bottom_box)

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
        if hasattr(self, "_icon_button"):
            self._detail_icon = _image_from_gicon(app.gicon, 96)
            self._icon_button.set_child(self._detail_icon)
        self._detail_title.set_label(app.name)
        self._name_reset_btn.set_visible(app.custom_name)

        is_shadowed = (
            app.has_stock
            and bool(app.stock_filename)
            and bool(app.filename)
            and Path(app.filename).resolve() != Path(app.stock_filename).resolve()
        )

        while child := self._badge_box.get_first_child():
            self._badge_box.remove(child)

        source_lbl = Gtk.Label(label=app.source)
        source_lbl.add_css_class("caption")
        source_lbl.add_css_class("dim-label")
        self._badge_box.append(source_lbl)

        if app.custom_icon:
            icon_lbl = Gtk.Label(label="·  Custom Icon")
            icon_lbl.add_css_class("caption")
            icon_lbl.add_css_class("accent")
            self._badge_box.append(icon_lbl)

        if app.custom_name:
            name_lbl = Gtk.Label(label="·  Custom Name")
            name_lbl.add_css_class("caption")
            name_lbl.add_css_class("accent")
            self._badge_box.append(name_lbl)

        if is_shadowed and not app.custom_icon and not app.custom_name:
            shadow_lbl = Gtk.Label(label="·  Override")
            shadow_lbl.add_css_class("caption")
            shadow_lbl.add_css_class("dim-label")
            self._badge_box.append(shadow_lbl)

        self._revert_button.set_visible(app.custom_icon)
        self._stock_button.set_visible(is_shadowed)
        self._bottom_box.set_visible(app.custom_icon or is_shadowed)

        self._desktop_file_row.set_subtitle(app.filename or "No desktop file")
        self._open_desktop_file.set_sensitive(bool(app.filename))
        self._app_folder_row.set_subtitle(app.app_folder or "Unknown")
        can_launch = bool(app.command or app.filename)
        if in_flatpak():
            can_launch = False
            self._command_row.set_tooltip_text("Launching external applications is not supported from within Flatpak sandbox")
            self._launch_btn.set_tooltip_text("Launching external applications is not supported from within Flatpak sandbox")
        else:
            self._command_row.set_tooltip_text(None)
            self._launch_btn.set_tooltip_text("Launch application")
        self._command_row.set_subtitle(app.command or "No command")
        self._command_row.set_activatable(can_launch)
        self._launch_btn.set_sensitive(can_launch)

    def _on_edit_name_clicked(self, _widget: object) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return

        dialog = Adw.AlertDialog(
            heading=f"Edit display name for {app.name}",
            body="Enter a new display name. This will customize how the application appears in your application grid and menus.",
        )
        entry = Gtk.Entry(text=app.name)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "rename":
                new_name = entry.get_text().strip()
                if not new_name:
                    self._toast("Display name cannot be empty.")
                    return
                if new_name == app.name:
                    return
                source_desktop = app.filename or app.stock_filename
                if not source_desktop:
                    self._toast("No desktop file found to override.")
                    return
                try:
                    apply_name(app.desktop_id, source_desktop, new_name)
                except OverrideError as exc:
                    self._toast(str(exc))
                    return
                self.reload()
                self._toast("Display name saved. Log out to refresh the app grid.")

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_name_reset(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        try:
            revert_name(self._detail_id)
        except OverrideError as exc:
            self._toast(str(exc))
            return
        self.reload()
        self._toast("Original display name restored.")

    def _apply_icon_path(self, app: AppEntry, path: str) -> bool:
        source_desktop = app.filename or app.stock_filename
        if not source_desktop:
            self._toast("No desktop file found to override.")
            return False
        try:
            apply_icon(app.desktop_id, source_desktop, path)
        except OverrideError as exc:
            self._toast(str(exc))
            return False
        self.reload()
        self._toast("Icon saved. Log out to refresh the app grid.")
        return True

    def _on_icon_dropped(self, _target: Gtk.DropTarget, value: object, _x: float, _y: float) -> bool:
        if not self._detail_id or not isinstance(value, Gio.File):
            return False
        path = value.get_path()
        if not path:
            return False
        app = self._find(self._detail_id)
        if app is None:
            return False
        return self._apply_icon_path(app, path)

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
        path = file.get_path()
        if not path:
            return
        app = self._find(self._detail_id)
        if app is None:
            return
        self._apply_icon_path(app, path)

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

    def _on_restore_stock(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return
        dialog = Adw.AlertDialog(
            heading=f"Restore default launcher for {app.name}?",
            body=(
                "This will remove your local overrides and restore the application "
                "to its original stock name and icon as provided by the package install."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore Default")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "restore":
                try:
                    restore_stock_launcher(app.desktop_id)
                except OverrideError as exc:
                    self._toast(str(exc))
                    return
                self.reload()
                self._toast(f"Restored default launcher for {app.name}.")

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_launch(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return

        if in_flatpak():
            self._toast("Launching external applications is not supported from within Flatpak sandbox.")
            return

        info = GioUnix.DesktopAppInfo.new(app.desktop_id)
        if not info and app.filename:
            info = GioUnix.DesktopAppInfo.new_from_filename(app.filename)
        if not info and app.stock_filename:
            info = GioUnix.DesktopAppInfo.new_from_filename(app.stock_filename)
        if not info:
            self._toast("Could not create launcher.")
            return
        try:
            info.launch([], None)
            self._toast(f"Launched {app.name}.")
        except Exception as exc:
            self._toast(f"Failed to launch: {exc}")

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
        launcher.launch(self, None, self._on_open_folder_done)

    def _on_open_folder_done(self, launcher: Gtk.FileLauncher, result: Gio.AsyncResult) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.GError:
            self._toast("Could not open Files.")

    def _on_open_file_done(self, launcher: Gtk.FileLauncher, result: Gio.AsyncResult) -> None:
        try:
            launcher.open_containing_folder_finish(result)
        except GLib.GError:
            self._toast("Could not open Files.")

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message))
