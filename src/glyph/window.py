import logging
import os
from pathlib import Path
import subprocess
import threading

import gi

gi.require_version("GioUnix", "2.0")

from gi.repository import Adw, Gdk, Gio, GioUnix, GLib, GObject, Gtk, Pango

import glyph.appimage as appimage
from glyph.catalog import AppEntry, list_apps, resolve_app_sources
from glyph import dialogs
import glyph.guidance as guidance
import glyph.launcher as launcher
from glyph.paths import in_flatpak
import glyph.sources as sources
from glyph.overrides import (
    LauncherModifiedExternallyError,
    OverrideError,
    apply_command,
    apply_icon,
    apply_name,
    apply_theme_icon,
    can_delete_managed_icon,
    discard_override,
    load_state,
    restore_stock_launcher,
    revert_command,
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
    overrides_section.append("Add AppImage…", "win.add-appimage")
    overrides_section.append("Find AppImages to Register…", "win.find-appimages")
    if os.environ.get("APPIMAGE"):
        overrides_section.append("Integrate Glyph into Desktop Grid…", "win.integrate-glyph")
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
        self._source_refresh_generation = 0
        self._source_refresh_running = False

        add_appimage_action = Gio.SimpleAction.new("add-appimage", None)
        add_appimage_action.connect("activate", self._on_add_appimage)
        self.add_action(add_appimage_action)

        find_appimages_action = Gio.SimpleAction.new("find-appimages", None)
        find_appimages_action.connect("activate", self._on_find_appimages)
        self.add_action(find_appimages_action)

        integrate_action = Gio.SimpleAction.new("integrate-glyph", None)
        integrate_action.connect("activate", self._on_integrate_glyph)
        self.add_action(integrate_action)

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

    def _on_find_appimages(self, *_args) -> None:
        try:
            state = load_state()
        except OverrideError as exc:
            self._toast(str(exc))
            return

        existing_targets: set[str] = set()
        for app in self._apps:
            if app.appimage_path:
                existing_targets.add(app.appimage_path)
                try:
                    existing_targets.add(str(Path(app.appimage_path).resolve()))
                except (OSError, RuntimeError):
                    pass
            if app.command:
                target = sources.is_appimage_target(app.command)
                if target:
                    existing_targets.add(target)
                    try:
                        existing_targets.add(str(Path(target).resolve()))
                    except (OSError, RuntimeError):
                        pass
        for record in state.values():
            for key in ("appimage_managed", "appimage_source"):
                path = record.get(key)
                if path:
                    existing_targets.add(path)
                    try:
                        existing_targets.add(str(Path(path).resolve()))
                    except (OSError, RuntimeError):
                        pass

        candidates = appimage.discover_registration_candidates(existing_targets)
        self.nav.push(self._build_appimage_candidates_page(candidates))

    def _build_appimage_candidates_page(
        self,
        candidates: list[appimage.AppImageCandidate],
    ) -> Adw.NavigationPage:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(16)
        content.set_margin_bottom(20)
        content.set_margin_start(16)
        content.set_margin_end(16)

        if candidates:
            intro = Gtk.Label(
                label="AppImages in ~/Applications that do not appear in your app list.",
                xalign=0,
                wrap=True,
            )
            intro.add_css_class("dim-label")
            content.append(intro)

            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            for candidate in candidates:
                listbox.append(self._make_candidate_row(candidate))
            content.append(listbox)
        else:
            empty = Adw.StatusPage()
            empty.set_icon_name("application-x-executable-symbolic")
            empty.set_title("No AppImages to Register")
            empty.set_description("No unregistered AppImages were found in ~/Applications.")
            content.append(empty)

        add_button = Gtk.Button(label="Add AppImage…", css_classes=["suggested-action"])
        add_button.set_halign(Gtk.Align.CENTER)
        add_button.connect("clicked", lambda *_: self._on_add_appimage())
        content.append(add_button)

        clamp = Adw.Clamp(maximum_size=640)
        clamp.set_child(content)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)

        return Adw.NavigationPage(title="AppImages to Register", child=toolbar)

    def _make_candidate_row(self, candidate: appimage.AppImageCandidate) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(candidate.display_name)
        row.set_subtitle(f"{candidate.path.name} · ~/Applications")
        row.add_prefix(_image_from_gicon(None, 32))
        button = Gtk.Button(label="Register…", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        button.connect("clicked", lambda _button: self._on_register_candidate(candidate))
        row.add_suffix(button)
        row.set_activatable(False)
        return row

    def _on_register_candidate(self, candidate: appimage.AppImageCandidate) -> None:
        try:
            plan = appimage.plan_appimage_registration(candidate.path)
        except (appimage.AppImageError, ValueError) as exc:
            self._toast(f"Cannot register AppImage: {exc}")
            return
        self._show_appimage_registration_preview(plan, open_detail=False)

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
        self._apps = list_apps(state, resolve_source_metadata=False)
        self._update_filter_menu()
        self._rebuild_list()
        self._request_source_metadata_refresh()
        if getattr(self, "_detail_id", None) and getattr(self, "_detail_page", None) is not None:
            app = self._find(self._detail_id)
            if app is not None:
                self._detail_page.set_title(app.name)
                self._fill_detail(app)

    def _request_source_metadata_refresh(self) -> None:
        self._source_refresh_generation += 1
        if self._source_refresh_running:
            return
        self._start_source_metadata_refresh(self._source_refresh_generation)

    def _start_source_metadata_refresh(self, generation: int) -> None:
        self._source_refresh_running = True
        apps = list(self._apps)

        def refresh() -> None:
            try:
                metadata = resolve_app_sources(apps)
            except Exception:
                logging.exception("Could not resolve application package metadata")
                metadata = None
            GLib.idle_add(self._apply_source_metadata, generation, metadata)

        threading.Thread(target=refresh, name="glyph-source-metadata", daemon=True).start()

    def _apply_source_metadata(self, generation: int, metadata: dict | None) -> bool:
        self._source_refresh_running = False
        if generation != self._source_refresh_generation:
            self._start_source_metadata_refresh(self._source_refresh_generation)
            return GLib.SOURCE_REMOVE
        if metadata is None:
            return GLib.SOURCE_REMOVE

        for app in self._apps:
            info = metadata.get(app.desktop_id)
            if info is None:
                continue
            app.source = info.display_name
            app.package_kind = info.package_kind
            app.install_scope = info.install_scope
            app.management = info.management
            app.version = info.version
            app.version_provenance = info.version_provenance
            app.source_evidence = info.source_evidence
            app.is_appimage = info.package_kind == "appimage"

            row = self._rows.get(app.desktop_id)
            if row is not None:
                subtitle = app.source
                is_shadowed = (
                    app.has_stock
                    and bool(app.stock_filename)
                    and bool(app.filename)
                    and Path(app.filename).resolve() != Path(app.stock_filename).resolve()
                )
                if is_shadowed and not app.custom:
                    subtitle += " · Override"
                row.set_subtitle(subtitle)

        if self._detail_id:
            app = self._find(self._detail_id)
            if app is not None and getattr(self, "_detail_page", None) is not None:
                self._fill_detail(app)
        return GLib.SOURCE_REMOVE

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

    def _open_detail(self, app: AppEntry) -> None:
        self._detail_id = app.desktop_id
        page = self._build_detail_page(app)
        self.nav.push(page)

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        desktop_id = getattr(row, "desktop_id", None)
        if not desktop_id:
            return
        app = self._find(desktop_id)
        if app is None:
            return
        self._open_detail(app)

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

        group = Adw.PreferencesGroup(title="Application details")

        self._source_row = Adw.ActionRow(title="Package format")
        group.add(self._source_row)

        self._version_row = Adw.ActionRow(title="Version")
        group.add(self._version_row)

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

        self._appimage_row = Adw.ActionRow(title="AppImage binary")
        self._appimage_row.set_subtitle_lines(2)
        self._open_appimage_folder = Gtk.Button(icon_name="folder-open-symbolic")
        self._open_appimage_folder.add_css_class("flat")
        self._open_appimage_folder.set_valign(Gtk.Align.CENTER)
        self._open_appimage_folder.set_tooltip_text("Open in Files")
        self._open_appimage_folder.connect("clicked", self._on_open_appimage_folder)
        self._appimage_row.add_suffix(self._open_appimage_folder)
        group.add(self._appimage_row)

        self._command_row = Adw.ActionRow(title="Command")
        self._command_row.set_subtitle_lines(3)
        self._command_row.set_activatable(True)
        self._command_row.connect("activated", self._on_launch)

        self._command_reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self._command_reset_btn.add_css_class("flat")
        self._command_reset_btn.set_valign(Gtk.Align.CENTER)
        self._command_reset_btn.set_tooltip_text("Reset to original command")
        self._command_reset_btn.connect("clicked", self._on_command_reset)
        self._command_row.add_suffix(self._command_reset_btn)

        self._command_edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        self._command_edit_btn.add_css_class("flat")
        self._command_edit_btn.set_valign(Gtk.Align.CENTER)
        self._command_edit_btn.set_tooltip_text("Customize launch command")
        self._command_edit_btn.connect("clicked", self._on_edit_command_clicked)
        self._command_row.add_suffix(self._command_edit_btn)

        self._launch_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self._launch_btn.add_css_class("flat")
        self._launch_btn.set_valign(Gtk.Align.CENTER)
        self._launch_btn.set_tooltip_text("Launch application")
        self._launch_btn.connect("clicked", self._on_launch)
        self._command_row.add_suffix(self._launch_btn)
        group.add(self._command_row)
        box.append(group)

        self._guidance_group = Adw.PreferencesGroup(title="Updates")

        self._open_software_row = Adw.ActionRow(title="Open Software")
        self._open_software_row.set_subtitle("View and manage updates in the Software app")
        self._open_software_row.set_activatable(True)
        self._open_software_row.add_prefix(Gtk.Image.new_from_icon_name("system-software-install-symbolic"))
        self._open_software_row.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
        self._open_software_row.connect("activated", self._on_open_software)
        self._guidance_group.add(self._open_software_row)

        self._terminal_expander = Adw.ExpanderRow(title="Terminal update commands")
        self._terminal_expander.set_subtitle("Commands to check and apply updates")
        self._terminal_expander.add_prefix(Gtk.Image.new_from_icon_name("utilities-terminal-symbolic"))

        self._terminal_cmd_row = Adw.ActionRow()
        self._terminal_cmd_copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        self._terminal_cmd_copy_btn.add_css_class("flat")
        self._terminal_cmd_copy_btn.set_valign(Gtk.Align.CENTER)
        self._terminal_cmd_copy_btn.set_tooltip_text("Copy command to clipboard")
        self._terminal_cmd_copy_btn.connect("clicked", self._on_copy_terminal_cmd)
        self._terminal_cmd_row.add_suffix(self._terminal_cmd_copy_btn)
        self._terminal_expander.add_row(self._terminal_cmd_row)
        self._guidance_group.add(self._terminal_expander)

        self._guidance_info_row = Adw.ActionRow()
        self._guidance_info_row.set_subtitle_lines(0)
        self._guidance_group.add(self._guidance_info_row)

        box.append(self._guidance_group)

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

        self._unregister_appimage_btn = Gtk.Button(label="Unregister AppImage launcher")
        self._unregister_appimage_btn.add_css_class("destructive-action")
        self._unregister_appimage_btn.add_css_class("pill")
        self._unregister_appimage_btn.set_tooltip_text("Remove this launcher while preserving the AppImage binary files")
        self._unregister_appimage_btn.connect("clicked", self._on_unregister_appimage)
        self._bottom_box.append(self._unregister_appimage_btn)

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

        if getattr(app, "version", ""):
            ver_lbl = Gtk.Label(label=f"·  v{app.version}")
            ver_lbl.add_css_class("caption")
            ver_lbl.add_css_class("dim-label")
            self._badge_box.append(ver_lbl)

        if app.custom_icon:
            icon_kind = "Theme icon" if getattr(app, "is_theme_icon", False) else "Custom icon"
            icon_lbl = Gtk.Label(label=f"·  {icon_kind}")
            icon_lbl.add_css_class("caption")
            icon_lbl.add_css_class("accent")
            self._badge_box.append(icon_lbl)

        if app.custom_name:
            name_lbl = Gtk.Label(label="·  Custom name")
            name_lbl.add_css_class("caption")
            name_lbl.add_css_class("accent")
            self._badge_box.append(name_lbl)

        if getattr(app, "custom_command", False):
            cmd_lbl = Gtk.Label(label="·  Custom command")
            cmd_lbl.add_css_class("caption")
            cmd_lbl.add_css_class("accent")
            self._badge_box.append(cmd_lbl)

        if is_shadowed and not app.custom_icon and not app.custom_name and not getattr(app, "custom_command", False):
            shadow_lbl = Gtk.Label(label="·  Override")
            shadow_lbl.add_css_class("caption")
            shadow_lbl.add_css_class("dim-label")
            self._badge_box.append(shadow_lbl)

        if hasattr(self, "_source_row"):
            scope_raw = getattr(app, "install_scope", "unknown")
            scope_str = f" · {scope_raw.capitalize()} installation" if scope_raw != "unknown" else ""
            self._source_row.set_subtitle(f"{app.source}{scope_str}")

        if hasattr(self, "_version_row"):
            ver = getattr(app, "version", "")
            if ver:
                prov = f" ({app.version_provenance})" if getattr(app, "version_provenance", "") else ""
                self._version_row.set_subtitle(f"{ver}{prov}")
                self._version_row.set_visible(True)
            else:
                self._version_row.set_visible(False)

        if hasattr(self, "_appimage_row"):
            ai_path = getattr(app, "appimage_path", "")
            self._appimage_row.set_visible(bool(ai_path))
            if ai_path:
                self._appimage_row.set_subtitle(ai_path)

        if hasattr(self, "_command_reset_btn"):
            self._command_reset_btn.set_visible(bool(getattr(app, "custom_command", False)))

        if hasattr(self, "_guidance_group"):
            pkg_kind = getattr(app, "package_kind", "unknown")
            inst_scope = getattr(app, "install_scope", "unknown")
            ver = getattr(app, "version", "")
            g = guidance.get_update_guidance(package_kind=pkg_kind, install_scope=inst_scope, version=ver, name=app.name)

            self._guidance_group.set_title("Updates")
            self._guidance_group.set_description(None)

            if hasattr(self, "_open_software_row"):
                self._open_software_row.set_visible(g.has_software_tool)
                if g.has_software_tool:
                    tool_title = f"Open {g.software_tool_name}" if g.software_tool_name else "Open Software"
                    self._open_software_row.set_title(tool_title)

            has_cmd = bool(getattr(g, "terminal_command", ""))
            if hasattr(self, "_terminal_expander"):
                self._terminal_expander.set_visible(has_cmd)
                self._current_terminal_cmd = getattr(g, "terminal_command", "")
                if has_cmd:
                    self._terminal_cmd_row.set_title(g.terminal_command)
                    self._terminal_cmd_row.set_subtitle("Run in terminal to check and apply updates")

            if hasattr(self, "_guidance_info_row"):
                self._guidance_info_row.set_visible(not has_cmd and not g.has_software_tool)
                if not has_cmd and not g.has_software_tool:
                    if pkg_kind == "appimage":
                        self._guidance_info_row.set_title("Manual AppImage update")
                        self._guidance_info_row.set_subtitle(
                            "Download the updated AppImage and place it in ~/Applications to replace this version."
                        )
                    elif inst_scope == "user":
                        self._guidance_info_row.set_title("User-managed application")
                        self._guidance_info_row.set_subtitle(
                            "Updates are managed manually by replacing or updating the target script or binary."
                        )
                    elif inst_scope == "system":
                        self._guidance_info_row.set_title("System package")
                        self._guidance_info_row.set_subtitle(
                            "Updated along with your Linux distribution's system packages."
                        )
                    else:
                        self._guidance_info_row.set_title("Unverified source")
                        self._guidance_info_row.set_subtitle(
                            "Version and update status are unknown. Check the application's website or documentation."
                        )

            # Test stub backwards compatibility
            if hasattr(self, "_guidance_row"):
                self._guidance_row.set_title(g.title)
                self._guidance_row.set_subtitle(f"{g.summary}\n\n{g.instructions}")
            if hasattr(self, "_open_software_btn"):
                self._open_software_btn.set_visible(g.has_software_tool)

        self._revert_button.set_visible(app.custom_icon)
        self._stock_button.set_visible(is_shadowed)

        is_glyph_ai = False
        if hasattr(self, "_unregister_appimage_btn"):
            try:
                state = load_state()
                rec = state.get(app.desktop_id, {})
                is_glyph_ai = bool(rec.get("glyph_managed_appimage"))
            except Exception:
                is_glyph_ai = False
            self._unregister_appimage_btn.set_visible(is_glyph_ai)

        self._bottom_box.set_visible(app.custom_icon or is_shadowed or is_glyph_ai)

        self._desktop_file_row.set_subtitle(app.filename or "No desktop file")
        self._open_desktop_file.set_sensitive(bool(app.filename))
        self._app_folder_row.set_subtitle(app.app_folder or "Unknown")

        can_launch = bool(getattr(app, "command", "") or getattr(app, "filename", ""))
        if in_flatpak() and not launcher.can_launch_apps():
            can_launch = False
            self._command_row.set_tooltip_text("Launching external applications is not supported from within Flatpak sandbox")
            self._launch_btn.set_tooltip_text("Launching external applications is not supported from within Flatpak sandbox")
        else:
            self._command_row.set_tooltip_text(None)
            self._launch_btn.set_tooltip_text("Launch application")

        self._command_row.set_subtitle(getattr(app, "command", "") or "No command")
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

                def do_save_name(name: str, force: bool = False):
                    try:
                        apply_name(app.desktop_id, source_desktop, name, force=force)
                    except LauncherModifiedExternallyError:
                        dialogs.prompt_external_modification(
                            self,
                            app.name,
                            allow_overwrite=True,
                            on_overwrite=lambda: do_save_name(name, force=True),
                            on_discard=lambda: self._discard_overrides(app),
                        )
                        return
                    except OverrideError as exc:
                        self._toast(str(exc))
                        return
                    self.reload()
                    self._toast("Display name saved.")

                do_save_name(new_name, force=False)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_name_reset(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id) if hasattr(self, "_find") else None
        try:
            revert_name(self._detail_id)
        except LauncherModifiedExternallyError:
            app_name = app.name if app else "application"
            dialogs.prompt_external_modification(
                self,
                app_name,
                allow_overwrite=False,
                on_discard=lambda: self._discard_overrides(app),
            )
            return
        except OverrideError as exc:
            self._toast(str(exc))
            return
        self.reload()
        self._toast("Original display name restored.")

    def _apply_icon_path(self, app: AppEntry, path: str, *, force: bool = False, delete_icon: bool = False) -> bool:
        source_desktop = app.filename or app.stock_filename
        if not source_desktop:
            self._toast("No desktop file found to override.")
            return False
        try:
            apply_icon(app.desktop_id, source_desktop, path, force=force, delete_icon=delete_icon)
        except LauncherModifiedExternallyError:
            dialogs.prompt_external_modification(
                self,
                app.name,
                allow_overwrite=True,
                on_overwrite=lambda: self._apply_icon_path(app, path, force=True, delete_icon=delete_icon),
                on_discard=lambda: self._discard_overrides(app),
            )
            return False
        except OverrideError as exc:
            self._toast(str(exc))
            return False
        self.reload()
        self._toast("Icon saved.")
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
        if can_delete_managed_icon(app.desktop_id):
            dialogs.prompt_artwork_cleanup(
                self,
                app.name,
                heading=f"Replace artwork for {app.name}?",
                body="Glyph has a saved copy of custom artwork for this application. Would you like to keep the image in icon storage for later reuse, or delete it?",
                on_choice=lambda delete: self._apply_icon_path(app, path, delete_icon=delete),
            )
            return True
        return self._apply_icon_path(app, path)

    def _on_change(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return
        dialogs.show_icon_picker(self, app, on_open_file_callback=self._open_image_file_dialog)

    def _open_image_file_dialog(self) -> None:
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
        if can_delete_managed_icon(app.desktop_id):
            dialogs.prompt_artwork_cleanup(
                self,
                app.name,
                heading=f"Replace artwork for {app.name}?",
                body="Glyph has a saved copy of custom artwork for this application. Would you like to keep the image in icon storage for later reuse, or delete it?",
                on_choice=lambda delete: self._apply_icon_path(app, path, delete_icon=delete),
            )
        else:
            self._apply_icon_path(app, path)

    def _discard_overrides(self, app: AppEntry | None) -> None:
        if not app:
            return

        def do_discard(delete_icon: bool = False):
            try:
                res = discard_override(app.desktop_id, delete_icon=delete_icon)
            except OverrideError as exc:
                self._toast(str(exc))
                return
            self.reload()
            if res and res.get("icon_skipped"):
                if res.get("icon_skip_reason") == "modified":
                    self._toast(f"Discarded overrides for {app.name}. Modified artwork was preserved.")
                else:
                    self._toast(f"Discarded overrides for {app.name}. Custom artwork was preserved.")
            else:
                self._toast(f"Discarded overrides for {app.name}.")

        if can_delete_managed_icon(app.desktop_id):
            dialogs.prompt_artwork_cleanup(
                self,
                app.name,
                heading=f"Custom artwork for {app.name}",
                body=f"Glyph has a saved copy of custom artwork for “{app.name}”. Would you like to keep the image in icon storage for later reuse, or delete it?",
                on_choice=do_discard,
            )
        else:
            do_discard(delete_icon=False)

    def _on_revert(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id) if hasattr(self, "_find") else None

        def do_revert(delete_icon: bool = False):
            try:
                revert_icon(self._detail_id, delete_icon=delete_icon)
            except LauncherModifiedExternallyError:
                app_name = app.name if app else "application"
                dialogs.prompt_external_modification(
                    self,
                    app_name,
                    allow_overwrite=False,
                    on_discard=lambda: self._discard_overrides(app),
                )
                return
            except OverrideError as exc:
                self._toast(str(exc))
                return
            self.reload()
            self._toast("Original icon restored.")

        if can_delete_managed_icon(self._detail_id):
            dialogs.prompt_artwork_cleanup(
                self,
                app.name if app else "application",
                heading=f"Restore original icon for {app.name if app else 'application'}?",
                body="Would you like to keep the custom artwork in your icon storage for later reuse, or delete it?",
                on_choice=do_revert,
            )
        else:
            do_revert(delete_icon=False)

    def _on_restore_stock(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return

        def do_restore(force: bool = False, delete_icon: bool = False):
            try:
                restore_stock_launcher(app.desktop_id, force=force, delete_icon=delete_icon)
            except LauncherModifiedExternallyError:
                dialogs.prompt_external_modification(
                    self,
                    app.name,
                    allow_overwrite=True,
                    on_overwrite=lambda: do_restore(force=True, delete_icon=delete_icon),
                    on_discard=lambda: self._discard_overrides(app),
                )
                return
            except OverrideError as exc:
                self._toast(str(exc))
                return
            self.reload()
            self._toast(f"Restored default launcher for {app.name}.")

        if can_delete_managed_icon(app.desktop_id):
            dialog = Adw.AlertDialog(
                heading=f"Restore default launcher for {app.name}?",
                body=(
                    "This will remove your local overrides and restore the application "
                    "to its original stock launcher. Would you like to keep your saved custom artwork "
                    "in icon storage for later reuse, or delete it?"
                ),
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("keep", "Keep Artwork")
            dialog.add_response("delete", "Delete Artwork")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("keep")
            dialog.set_close_response("cancel")

            def on_response(_d, response):
                if response == "keep":
                    do_restore(force=False, delete_icon=False)
                elif response == "delete":
                    do_restore(force=False, delete_icon=True)

            dialog.connect("response", on_response)
            dialog.present(self)
        else:
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
                    do_restore(force=False, delete_icon=False)

            dialog.connect("response", on_response)
            dialog.present(self)

    def _on_edit_command_clicked(self, _widget: object) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return

        current_cmd = getattr(app, "command", "") or ""
        dialog = Adw.AlertDialog(
            heading=f"Customize launch command for {app.name}",
            body="Enter a new launch command. This will be saved to your user-level launcher override.",
        )
        entry = Gtk.Entry(text=current_cmd)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "save":
                new_cmd = entry.get_text().strip()
                if not new_cmd:
                    self._toast("Command cannot be empty.")
                    return
                if new_cmd == current_cmd:
                    return
                source_desktop = app.filename or app.stock_filename
                if not source_desktop:
                    self._toast("No desktop file found to override.")
                    return

                def do_save_cmd(cmd: str, force: bool = False):
                    try:
                        apply_command(app.desktop_id, source_desktop, cmd, force=force)
                    except LauncherModifiedExternallyError:
                        dialogs.prompt_external_modification(
                            self,
                            app.name,
                            allow_overwrite=True,
                            on_overwrite=lambda: do_save_cmd(cmd, force=True),
                            on_discard=lambda: self._discard_overrides(app),
                        )
                        return
                    except OverrideError as exc:
                        self._toast(str(exc))
                        return
                    self.reload()
                    self._toast("Custom command saved.")

                do_save_cmd(new_cmd, force=False)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_command_reset(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id) if hasattr(self, "_find") else None
        try:
            revert_command(self._detail_id)
        except LauncherModifiedExternallyError:
            app_name = app.name if app else "application"
            dialogs.prompt_external_modification(
                self,
                app_name,
                allow_overwrite=False,
                on_discard=lambda: self._discard_overrides(app),
            )
            return
        except OverrideError as exc:
            self._toast(str(exc))
            return
        self.reload()
        self._toast("Original command restored.")

    def _on_launch(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return
        try:
            launcher.launch(app)
            self._toast(f"Launched {app.name}.")
        except launcher.LaunchError as exc:
            self._toast(f"Failed to launch: {exc}")
        except Exception as exc:
            self._toast(f"Failed to launch: {exc}")

    def _on_open_software(self, *_) -> None:
        target_id = ""
        if self._detail_id:
            app = self._find(self._detail_id)
            if app:
                target_id = getattr(app, "flatpak_id", "") or app.desktop_id.removesuffix(".desktop")
            else:
                target_id = self._detail_id.removesuffix(".desktop")
        guidance.open_software_tool(target_id)

    def _on_copy_terminal_cmd(self, _button: Gtk.Button) -> None:
        cmd = getattr(self, "_current_terminal_cmd", "")
        if not cmd:
            return
        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(cmd)
            self._toast("Command copied to clipboard.")

    def _on_open_desktop_file(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None or not app.filename:
            return
        launcher_obj = Gtk.FileLauncher.new(Gio.File.new_for_path(app.filename))
        launcher_obj.open_containing_folder(self, None, self._on_open_file_done)

    def _on_open_app_folder(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None or not app.app_folder:
            return
        launcher_obj = Gtk.FileLauncher.new(Gio.File.new_for_path(app.app_folder))
        launcher_obj.launch(self, None, self._on_open_folder_done)

    def _on_open_appimage_folder(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return
        ai_path = getattr(app, "appimage_path", "")
        if not ai_path or not Path(ai_path).exists():
            self._toast("AppImage file not found.")
            return
        launcher_obj = Gtk.FileLauncher.new(Gio.File.new_for_path(ai_path))
        launcher_obj.open_containing_folder(self, None, self._on_open_file_done)

    def _on_unregister_appimage(self, _button: Gtk.Button) -> None:
        if not self._detail_id:
            return
        app = self._find(self._detail_id)
        if app is None:
            return

        def do_unregister(delete_icon: bool | None = None):
            try:
                if delete_icon is not None:
                    res = appimage.unregister_appimage(app.desktop_id, delete_icon=delete_icon)
                else:
                    res = appimage.unregister_appimage(app.desktop_id)
            except Exception as exc:
                self._toast(str(exc))
                return
            self.nav.pop()
            self._detail_id = None
            self.reload()
            if res and res.get("icon_kept"):
                self._toast(f"Unregistered launcher for {app.name}. Artwork and binary file preserved.")
            elif res and res.get("icon_skipped"):
                if res.get("icon_skip_reason") == "modified":
                    self._toast(f"Unregistered launcher for {app.name}. Modified artwork and binary file preserved.")
                else:
                    self._toast(f"Unregistered launcher for {app.name}. Artwork and binary file preserved.")
            else:
                self._toast(f"Unregistered launcher for {app.name}. Binary file preserved.")

        if can_delete_managed_icon(app.desktop_id):
            dialog = Adw.AlertDialog(
                heading=f"Unregister launcher for {app.name}?",
                body=(
                    "This will remove the desktop launcher created by Glyph while leaving the AppImage binary intact. "
                    "Would you like to keep the extracted artwork in your icon storage for later reuse, or delete it?"
                ),
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("keep", "Keep Artwork")
            dialog.add_response("delete", "Delete Artwork")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("keep")
            dialog.set_close_response("cancel")

            def on_response(_d, response):
                if response == "keep":
                    do_unregister(delete_icon=False)
                elif response == "delete":
                    do_unregister(delete_icon=True)

            dialog.connect("response", on_response)
            dialog.present(self)
        else:
            dialog = Adw.AlertDialog(
                heading=f"Unregister launcher for {app.name}?",
                body=(
                    "This will remove the desktop launcher created by Glyph, "
                    "leaving the AppImage binary files intact in your Applications folder."
                ),
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("unregister", "Unregister Launcher")
            dialog.set_response_appearance("unregister", Adw.ResponseAppearance.DESTRUCTIVE)

            def on_response(_d, response):
                if response == "unregister":
                    do_unregister()

            dialog.connect("response", on_response)
            dialog.present(self)

    def _on_add_appimage(self, *_args) -> None:
        dialog = Gtk.FileDialog(title="Choose an AppImage")
        filter_ai = Gtk.FileFilter()
        filter_ai.set_name("AppImage Files")
        filter_ai.add_pattern("*.AppImage")
        filter_ai.add_pattern("*.appimage")
        filter_ai.add_mime_type("application/vnd.appimage")
        filter_ai.add_mime_type("application/x-iso9660-appimage")
        filter_ai.add_mime_type("application/x-executable")

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All Files")
        filter_all.add_pattern("*")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_ai)
        filters.append(filter_all)
        dialog.set_filters(filters)

        def on_open_done(dlg: Gtk.FileDialog, res: Gio.AsyncResult):
            try:
                file = dlg.open_finish(res)
            except GLib.GError:
                return
            if not file:
                return
            path_str = file.get_path()
            if not path_str:
                self._toast("Selected file is not available as a local path.")
                return
            source_file = Path(path_str)
            try:
                plan = appimage.plan_appimage_registration(source_file)
            except (appimage.AppImageError, ValueError) as exc:
                self._toast(f"Cannot register AppImage: {exc}")
                return
            self._show_appimage_registration_preview(plan)

        dialog.open(self, None, on_open_done)

    def _show_appimage_registration_preview(
        self,
        plan: appimage.AppImageRegistrationPlan,
        *,
        open_detail: bool = True,
    ) -> None:
        app_name = plan.display_name
        body_lines = [
            f"Source: {plan.source_file}",
        ]
        if not plan.is_in_place:
            body_lines.append(f"Destination: {plan.managed_file} (copy to ~/Applications)")
            if plan.managed_collision:
                body_lines.append("\n⚠️ Warning: A file with this name already exists in ~/Applications.")
        else:
            body_lines.append(f"Location: {plan.managed_file} (already in ~/Applications)")

        body_lines.append(f"Launcher: {plan.launcher_path.name}")
        if plan.launcher_collision:
            body_lines.append("\n⚠️ Warning: A desktop launcher with this ID already exists.")

        body_lines.append("\nThe original source file will remain untouched.")

        dialog = Adw.AlertDialog(
            heading=f"Register AppImage for {app_name}?",
            body="\n".join(body_lines),
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        name_entry = Gtk.Entry(text=app_name)
        name_entry.set_placeholder_text("Application Name")
        box.append(name_entry)

        launcher_info = Gtk.Label(
            label=f"Desktop launcher: {plan.desktop_id}",
            xalign=0,
            css_classes=["dim-label", "caption"],
        )
        box.append(launcher_info)

        collision_warning = Gtk.Label(
            label="",
            xalign=0,
            wrap=True,
            css_classes=["error", "caption"],
        )
        collision_warning.set_visible(False)
        box.append(collision_warning)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("register", "Register AppImage")

        def update_state() -> None:
            chosen = name_entry.get_text().strip() or app_name
            derived_id = appimage.derive_desktop_id(chosen)
            launcher_info.set_text(f"Desktop launcher: {derived_id}")
            candidate_launcher = appimage.APPLICATIONS_DIR / derived_id

            has_launcher_col = candidate_launcher.exists()
            has_col = plan.managed_collision or has_launcher_col
            if has_launcher_col:
                collision_warning.set_text("⚠️ A desktop launcher with this ID already exists.")
                collision_warning.set_visible(True)
            elif plan.managed_collision:
                collision_warning.set_text("⚠️ A file with this name already exists in ~/Applications.")
                collision_warning.set_visible(True)
            else:
                collision_warning.set_visible(False)

            if has_col:
                dialog.set_default_response("cancel")
            else:
                dialog.set_default_response("register")

        name_entry.connect("changed", lambda _e: update_state())
        update_state()
        dialog.set_close_response("cancel")

        def on_resp(_d, resp):
            if resp == "register":
                chosen = name_entry.get_text().strip() or app_name
                try:
                    result_id = appimage.register_appimage(plan, chosen_name=chosen)
                except (appimage.AppImageError, OverrideError, OSError) as exc:
                    self._toast(f"Registration failed: {exc}")
                    return
                self.reload()
                self._toast(f"Registered {chosen} in application grid.")
                app = self._find(result_id)
                if open_detail and app is not None:
                    self._open_detail(app)
                elif not open_detail:
                    self.nav.pop()

        dialog.connect("response", on_resp)
        dialog.present(self)

    def _on_integrate_glyph(self, *_args) -> None:
        ai_env = os.environ.get("APPIMAGE")
        if not ai_env:
            self._toast("Glyph is not currently running from an AppImage.")
            return
        ai_path = Path(ai_env)
        if not ai_path.is_file():
            self._toast(f"AppImage file not found at {ai_path}.")
            return
        try:
            plan = appimage.plan_appimage_registration(ai_path)
        except (appimage.AppImageError, ValueError) as exc:
            self._toast(f"Cannot integrate Glyph: {exc}")
            return
        self._show_appimage_registration_preview(plan)

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
