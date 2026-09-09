"""Interactive v0.2.0 UI study. All draft state is memory-only.

Deliberately does not import catalog, overrides, backups, or launching code.
Icons are a fixed illustrative set, not the result of an installed-icon scan.
Run independently with: PYTHONPATH=src python3 -m glyph.preview
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk, Pango


@dataclass(frozen=True)
class SampleIcon:
    name: str
    icon: str
    color: str
    collection: str


# Collection names intentionally do not claim to be installed host themes.
SAMPLE_ICONS = (
    SampleIcon("Web browser", "web-browser-symbolic", "blue", "Applications"),
    SampleIcon("Terminal", "utilities-terminal-symbolic", "slate", "Applications"),
    SampleIcon("Files", "folder-symbolic", "amber", "Places"),
    SampleIcon("Music", "audio-x-generic-symbolic", "pink", "Applications"),
    SampleIcon("Photos", "image-x-generic-symbolic", "green", "Applications"),
    SampleIcon("Settings", "emblem-system-symbolic", "slate", "Applications"),
    SampleIcon("Documents", "folder-documents-symbolic", "blue", "Places"),
    SampleIcon("Downloads", "folder-download-symbolic", "green", "Places"),
    SampleIcon("Videos", "video-x-generic-symbolic", "purple", "Applications"),
    SampleIcon("Calendar", "x-office-calendar-symbolic", "pink", "Applications"),
    SampleIcon("Mail", "mail-unread-symbolic", "blue", "Applications"),
    SampleIcon("Favorites", "starred-symbolic", "amber", "Places"),
)

CSS = b"""
.glyph-preview .preview-intro { font-size: 30px; font-weight: 800; }
.glyph-preview .preview-eyebrow { letter-spacing: 2px; font-size: 11px; font-weight: 700; }
.glyph-preview .preview-swatch { border-radius: 18px; padding: 16px; }
.glyph-preview .blue { background: #dbeafe; color: #2159a8; }
.glyph-preview .slate { background: #e5e7eb; color: #374151; }
.glyph-preview .amber { background: #fff0c2; color: #926300; }
.glyph-preview .pink { background: #fce0ea; color: #a72d62; }
.glyph-preview .green { background: #dcf3e5; color: #247345; }
.glyph-preview .purple { background: #eae0fb; color: #7243b0; }
.glyph-preview .icon-tile { padding: 10px 4px; border-radius: 14px; }
.glyph-preview .icon-tile:checked { background: alpha(@accent_color, .14); box-shadow: inset 0 0 0 2px @accent_color; }
.glyph-preview .preview-surface { border-radius: 14px; padding: 16px; }
.glyph-preview .preview-light { background: #f6f5f4; color: #252525; }
.glyph-preview .preview-dark { background: #252529; color: #fafafa; }
.glyph-preview .preview-footer { padding: 12px 18px; }
.glyph-preview .preview-note { font-size: 12px; }
"""


def label(text: str, style: str = "", *, center: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0.5 if center else 0)
    widget.set_wrap(True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if style:
        widget.add_css_class(style)
    return widget


def image(name: str, size: int) -> Gtk.Image:
    widget = Gtk.Image.new_from_icon_name(name)
    widget.set_pixel_size(size)
    return widget


def swatch(sample: SampleIcon, size: int = 32) -> Gtk.Box:
    box = Gtk.Box()
    box.set_halign(Gtk.Align.CENTER)
    box.add_css_class("preview-swatch")
    box.add_css_class(sample.color)
    box.append(image(sample.icon, size))
    return box


def action_row(title: str, subtitle: str, icon: str, callback) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.set_title_lines(2)
    row.set_subtitle_lines(3)
    row.add_prefix(image(icon, 24))
    row.add_suffix(image("go-next-symbolic", 16))
    row.set_activatable(True)
    row.connect("activated", lambda *_: callback())
    return row


class PreviewWindow(Adw.ApplicationWindow):
    """Separate window prevents demo values from entering the live app model."""

    def __init__(self, *, application=None, transient_for=None, initial="home"):
        super().__init__(application=application, transient_for=transient_for)
        self.set_title("Glyph · v0.2.0 preview")
        self.set_default_size(620, 780)
        self.add_css_class("glyph-preview")
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        self._css_provider = provider
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.connect("destroy", self._remove_css)
        self.selected_icon = SAMPLE_ICONS[1]
        self.icon_mode = "Keep this artwork"
        self.draft_name = ""
        self.draft_executable = ""
        self.draft_description = ""
        self.draft_directory = ""
        self.draft_terminal = False
        self.draft_arguments: list[str] = []
        self._icon_target = "library"
        self._icon_buttons: list[tuple[Gtk.FlowBoxChild, Gtk.ToggleButton, SampleIcon]] = []
        self.nav = Adw.NavigationView()
        self.set_content(self.nav)
        self.nav.add(self._home())
        if initial == "editor":
            self.show_editor()

    def _remove_css(self, *_args):
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self._css_provider)

    def _page(self, title: str, *, footer: Gtk.Widget | None = None):
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        banner = Adw.Banner(title="UI preview · Sample data · Nothing is saved or run")
        banner.set_revealed(True)
        toolbar.add_top_bar(banner)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(18)
        content.set_margin_end(18)
        clamp = Adw.Clamp(maximum_size=580)
        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(clamp)
        scroll.set_vexpand(True)
        toolbar.set_content(scroll)
        if footer is not None:
            footer.add_css_class("preview-footer")
            toolbar.add_bottom_bar(footer)
        return Adw.NavigationPage(title=title, child=toolbar), content

    def _footer(self, title: str, callback=None, *, enabled=True):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        button = Gtk.Button(label=title)
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.set_sensitive(enabled)
        if callback:
            button.connect("clicked", lambda *_: callback())
        box.append(button)
        return box, button

    def _home(self):
        page, body = self._page("Glyph")
        body.append(label("GLYPH / 0.2.0", "preview-eyebrow"))
        body.append(label("Find an icon.\nCreate a launcher.\nMake it yours.", "preview-intro"))
        body.append(label("A place for the little details that make your desktop feel like home.", "dim-label"))
        group = Adw.PreferencesGroup(title="Make it yours")
        group.add(action_row("Browse icons", "Find a new look in your icon collection.", "view-grid-symbolic", self.show_icons))
        group.add(action_row("Create a launcher", "Give a command a name, an icon, and a place in your app menu.", "list-add-symbolic", self.show_editor))
        body.append(group)
        care = Adw.PreferencesGroup(title="Keep it working", description="Example review screens for the next implementation phase.")
        care.add(action_row("Needs attention", "3 examples · An update, a missing icon, and a moved app", "dialog-warning-symbolic", self.show_attention))
        care.add(action_row("Restore a backup", "Preview changes and custom launchers before restoring.", "document-revert-symbolic", self.show_backup))
        body.append(care)
        body.append(label("Explore the screens freely. This preview uses illustrative icons and launcher examples; it does not inspect or change your applications.", "preview-note"))
        return page

    def show_icons(self, target="library"):
        self._icon_target = target
        footer, self._use_icon = self._footer("Use in draft" if target == "editor" else "Select preview icon", self._choose_icon)
        page, body = self._page("Choose an icon", footer=footer)
        body.append(label("A new look, already close by.", "title-1"))
        body.append(label("Installed-icon browser layout · Illustrative collection", "dim-label"))
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.icon_search = Gtk.SearchEntry(placeholder_text="Search preview icons")
        self.icon_search.connect("search-changed", self._filter_icons)
        controls.append(self.icon_search)
        filters = Gtk.Box(spacing=8)
        self.collection = Gtk.DropDown.new_from_strings(["All icons", "Applications", "Places"])
        self.collection.set_hexpand(True)
        self.collection.set_tooltip_text("Filter preview collection")
        self.collection.update_property([Gtk.AccessibleProperty.LABEL], ["Icon collection"])
        self.collection.connect("notify::selected", self._filter_icons)
        filters.append(self.collection)
        file_button = Gtk.Button(label="Choose file…")
        file_button.set_sensitive(False)
        file_button.set_tooltip_text("File selection will be connected during implementation")
        filters.append(file_button)
        controls.append(filters)
        body.append(controls)
        self.icon_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        self.icon_grid.set_min_children_per_line(2)
        self.icon_grid.set_max_children_per_line(4)
        self.icon_grid.set_homogeneous(True)
        self.icon_grid.set_row_spacing(6)
        self.icon_grid.set_column_spacing(6)
        self._icon_buttons = []
        for sample in SAMPLE_ICONS:
            button = Gtk.ToggleButton()
            button.add_css_class("flat")
            button.add_css_class("icon-tile")
            button.set_tooltip_text(sample.name)
            button.update_property([Gtk.AccessibleProperty.LABEL], [sample.name])
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            tile.append(swatch(sample))
            name = label(sample.name, "caption", center=True)
            name.set_max_width_chars(12)
            tile.append(name)
            button.set_child(tile)
            child = Gtk.FlowBoxChild()
            child.set_child(button)
            self.icon_grid.append(child)
            button.set_active(sample == self.selected_icon)
            button.connect("clicked", self._select_icon, sample)
            self._icon_buttons.append((child, button, sample))
        body.append(self.icon_grid)
        self.no_icons = label("No matching icons. Try another name or collection.", "dim-label", center=True)
        self.no_icons.set_visible(False)
        body.append(self.no_icons)
        self.icon_preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.append(self.icon_preview)
        self._render_icon_preview()
        mode = Adw.PreferencesGroup(title="When your desktop theme changes")
        choice = Adw.ComboRow(title="Icon behavior", model=Gtk.StringList.new(["Keep this artwork", "Follow the theme"]))
        choice.set_selected(0 if self.icon_mode == "Keep this artwork" else 1)
        choice.connect("notify::selected", lambda row, *_: setattr(self, "icon_mode", row.get_selected_item().get_string()))
        mode.add(choice)
        body.append(mode)
        body.append(label("Keep this artwork preserves the selected image. Follow the theme uses an icon name, so its appearance can change.", "preview-note"))
        self.nav.push(page)

    def _filter_icons(self, *_args):
        query = self.icon_search.get_text().strip().casefold()
        collection = self.collection.get_selected_item().get_string()
        visible = 0
        for child, _button, sample in self._icon_buttons:
            show = query in sample.name.casefold() and (collection == "All icons" or sample.collection == collection)
            child.set_visible(show)
            visible += int(show)
        self.no_icons.set_visible(visible == 0)

    def _select_icon(self, selected, sample):
        self.selected_icon = sample
        for _child, button, _sample in self._icon_buttons:
            button.set_active(button is selected)
        self._render_icon_preview()

    def _render_icon_preview(self):
        while child := self.icon_preview.get_first_child():
            self.icon_preview.remove(child)
        self.icon_preview.append(label(self.selected_icon.name, "heading"))
        surfaces = Gtk.Box(spacing=10, homogeneous=True)
        for theme in ("light", "dark"):
            surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            surface.add_css_class("preview-surface")
            surface.add_css_class(f"preview-{theme}")
            sizes = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            for size in (24, 40):
                sizes.append(image(self.selected_icon.icon, size))
            surface.append(sizes)
            surface.append(label(theme.capitalize(), "caption", center=True))
            surfaces.append(surface)
        self.icon_preview.append(surfaces)

    def _choose_icon(self):
        self.nav.pop()
        if self._icon_target == "editor":
            self._update_draft_hero()

    def show_editor(self):
        footer, self.review_button = self._footer("Review launcher", self.show_launcher_review)
        page, body = self._page("New launcher", footer=footer)
        self.draft_hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.append(self.draft_hero)
        self._update_draft_hero()
        choose = Gtk.Button(label="Choose icon…", halign=Gtk.Align.CENTER)
        choose.add_css_class("pill")
        choose.connect("clicked", lambda *_: self.show_icons("editor"))
        body.append(choose)
        basics = Adw.PreferencesGroup(title="The essentials", description="A name for your app menu and the program it should open.")
        self.name_entry = Adw.EntryRow(title="Name", text=self.draft_name)
        self.name_entry.connect("changed", self._draft_changed, "draft_name")
        basics.add(self.name_entry)
        self.executable_entry = Adw.EntryRow(title="Executable or command name", text=self.draft_executable)
        self.executable_entry.connect("changed", self._draft_changed, "draft_executable")
        basics.add(self.executable_entry)
        description = Adw.EntryRow(title="Description (optional)", text=self.draft_description)
        description.connect("changed", self._draft_changed, "draft_description")
        basics.add(description)
        body.append(basics)
        body.append(label("For example: /home/you/Applications/MyApp.AppImage. This preview does not check paths or interpret commands.", "preview-note"))
        advanced_group = Adw.PreferencesGroup()
        advanced = Adw.ExpanderRow(title="Launch options", subtitle="Arguments, working directory, and terminal")
        directory = Adw.EntryRow(title="Working directory (optional)", text=self.draft_directory)
        directory.connect("changed", self._draft_changed, "draft_directory")
        advanced.add_row(directory)
        terminal = Adw.SwitchRow(title="Run in terminal", active=self.draft_terminal)
        terminal.connect("notify::active", lambda row, *_: setattr(self, "draft_terminal", row.get_active()))
        advanced.add_row(terminal)
        # Draft arguments stay an ordered list; no shell splitting or serialization.
        for value in self.draft_arguments:
            self._add_argument(advanced, value, append=False)
        add = Adw.ActionRow(title="Add argument", subtitle="One argument per field; spaces stay within that argument.")
        add.add_prefix(image("list-add-symbolic", 16))
        add.set_activatable(True)
        add.connect("activated", lambda *_: self._add_argument(advanced))
        advanced.add_row(add)
        advanced_group.add(advanced)
        body.append(advanced_group)
        self.editor_note = label("Enter a name and executable to explore the review screen.", "preview-note")
        body.append(self.editor_note)
        self._update_review_sensitivity()
        self.nav.push(page)

    def _add_argument(self, expander, value="", *, append=True):
        if append:
            self.draft_arguments.append(value)
            index = len(self.draft_arguments) - 1
        else:
            index = getattr(expander, "_argument_count", 0)
        expander._argument_count = index + 1
        row = Adw.EntryRow(title=f"Argument {index + 1}", text=value)
        row.connect("changed", lambda entry: self.draft_arguments.__setitem__(index, entry.get_text()))
        expander.add_row(row)

    def _draft_changed(self, entry, field):
        setattr(self, field, entry.get_text())
        self._update_draft_hero()
        self._update_review_sensitivity()

    def _update_draft_hero(self):
        while child := self.draft_hero.get_first_child():
            self.draft_hero.remove(child)
        self.draft_hero.append(swatch(self.selected_icon, 48))
        self.draft_hero.append(label(self.draft_name.strip() or "Your next launcher", "title-1", center=True))
        self.draft_hero.append(label("A little shortcut to something useful.", "dim-label", center=True))

    def _update_review_sensitivity(self):
        self.review_button.set_sensitive(bool(self.draft_name.strip() and self.draft_executable.strip()))

    def show_launcher_review(self):
        footer, _button = self._footer("Create launcher", enabled=False)
        footer.append(label("Saving will be connected in the implementation phase.", "preview-note", center=True))
        page, body = self._page("Review launcher", footer=footer)
        body.append(swatch(self.selected_icon, 48))
        body.append(label(self.draft_name, "title-1", center=True))
        body.append(label("Check the details before adding it to your app menu.", "dim-label", center=True))
        group = Adw.PreferencesGroup(title="Launcher details")
        pairs = [("Name", self.draft_name), ("Executable", self.draft_executable)]
        pairs.extend((f"Argument {i + 1}", arg or "(empty argument)") for i, arg in enumerate(self.draft_arguments))
        pairs.extend([("Working directory", self.draft_directory or "Default"), ("Run in terminal", "Yes" if self.draft_terminal else "No"), ("Icon", f"{self.selected_icon.name} · {self.icon_mode}")])
        if self.draft_description:
            pairs.append(("Description", self.draft_description))
        for title, subtitle in pairs:
            group.add(self._detail_row(title, subtitle))
        body.append(group)
        body.append(label("Creating a launcher will not run it. It will be available from your desktop’s application menu.", "preview-note"))
        self.nav.push(page)

    @staticmethod
    def _detail_row(title, subtitle):
        # Draft values are plain text, never Pango markup.
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        row.set_subtitle_lines(0)
        return row

    def show_attention(self):
        page, body = self._page("Needs attention")
        body.append(label("Keep your changes.\nStay up to date.", "title-1"))
        body.append(label("Illustrative issues only. Your applications have not been checked.", "dim-label"))
        group = Adw.PreferencesGroup(title="3 example issues")
        group.add(action_row("Web browser", "The installed launcher changed. Your custom icon can stay.", "software-update-available-symbolic", lambda: self.show_repair("update")))
        group.add(action_row("Music", "The chosen icon is no longer available.", "image-missing-symbolic", lambda: self.show_repair("icon")))
        group.add(action_row("Studio", "The executable may have moved.", "folder-missing-symbolic", lambda: self.show_repair("target")))
        body.append(group)
        body.append(label("Nothing is repaired or removed automatically. You choose which changes to keep.", "preview-note"))
        self.nav.push(page)

    def show_repair(self, kind):
        footer, _button = self._footer("Apply repair", enabled=False)
        footer.append(label("Review only · No files will change", "preview-note", center=True))
        page, body = self._page("Review changes", footer=footer)
        if kind == "update":
            body.append(label("An update underneath.\nYour look on top.", "title-1"))
            body.append(label("Web browser · Example package update", "dim-label"))
            group = Adw.PreferencesGroup(title="What changed")
            group.add(self._detail_row("Previous command", "/usr/bin/example-browser %u"))
            group.add(self._detail_row("Updated command", "/usr/bin/example-browser --new-window %u"))
            group.add(self._detail_row("Your customization", "Custom icon and display name stay as they are."))
            body.append(group)
            choices = Adw.PreferencesGroup(title="How would you like to continue?")
            first = None
            for title, subtitle in [("Use the updated launcher", "Keep your icon and name; accept the package’s changes."), ("Keep the current launcher", "Leave this example unresolved for now.")]:
                row = self._detail_row(title, subtitle)
                check = Gtk.CheckButton()
                if first is None:
                    first = check
                    check.set_active(True)
                else:
                    check.set_group(first)
                row.add_prefix(check)
                row.set_activatable_widget(check)
                choices.add(row)
            body.append(choices)
        elif kind == "icon":
            body.append(label("Give Music its icon back.", "title-1"))
            body.append(label("The original icon file is missing in this example. The launcher and name are kept.", "dim-label"))
            group = Adw.PreferencesGroup(title="Choose a replacement")
            group.add(action_row("Browse icons", "Explore the preview collection.", "view-grid-symbolic", self.show_icons))
            body.append(group)
        else:
            body.append(label("Reconnect Studio.", "title-1"))
            body.append(label("If an application moved, its launcher can point to the new location.", "dim-label"))
            group = Adw.PreferencesGroup(title="Executable location")
            group.add(Adw.EntryRow(title="Executable", text="/home/you/Applications/Studio.AppImage"))
            body.append(group)
            body.append(label("Path checks are not connected. Flatpak may be unable to verify a host executable even when it exists.", "preview-note"))
        self.nav.push(page)

    def show_backup(self):
        footer, _button = self._footer("Restore selected", enabled=False)
        footer.append(label("Example archive · No backup was opened", "preview-note", center=True))
        page, body = self._page("Review backup", footer=footer)
        body.append(label("Bring your desktop details back.", "title-1"))
        body.append(label("2 customizations and 1 launcher · Sample backup", "dim-label"))
        group = Adw.PreferencesGroup(title="Customizations")
        for title, subtitle in [("Web browser", "Restore custom icon and display name"), ("Music", "Restore custom icon")]:
            row = self._detail_row(title, subtitle)
            check = Gtk.CheckButton(active=True)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            group.add(row)
        body.append(group)
        launchers = Adw.PreferencesGroup(title="Custom launcher", description="Launchers include commands. Review them before restoring.")
        launchers.add(self._detail_row("Studio", "/home/you/Applications/Studio.AppImage"))
        launchers.add(Adw.ComboRow(title="If the launcher already exists", model=Gtk.StringList.new(["Skip", "Create a separate copy"])))
        body.append(launchers)
        body.append(label("Restoring will not run any applications. Executables are not included in a backup, and their paths may differ on this computer.", "preview-note"))
        self.nav.push(page)


def present_preview(parent, *, initial="home"):
    preview = PreviewWindow(application=parent.get_application(), transient_for=parent, initial=initial)
    preview.present()
    return preview


def main():
    app = Adw.Application(application_id="io.github.the0megastar.Glyph.Preview", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", lambda application: PreviewWindow(application=application).present())
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
