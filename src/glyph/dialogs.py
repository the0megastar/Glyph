from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from glyph.icon_themes import ThemeIcon, list_installed_themes, search_theme_icons
from glyph.overrides import (
    LauncherModifiedExternallyError,
    OverrideError,
    apply_icon,
    apply_theme_icon,
    can_delete_managed_icon,
    discard_override,
    export_backup,
    import_backup,
    load_state,
    preview_backup,
    restore_all_to_stock,
    revert_all_icons,
    revert_all_names,
)


def _toast(parent: Gtk.Widget | None, message: str) -> None:
    if parent and hasattr(parent, "_toast"):
        parent._toast(message)


def _reload(parent: Gtk.Widget | None) -> None:
    if parent and hasattr(parent, "reload"):
        parent.reload()


def prompt_artwork_cleanup(
    parent: Gtk.Widget | None,
    app_name: str,
    heading: str,
    body: str,
    on_choice,
    on_cancel=None,
) -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("keep", "Keep Artwork")
    dialog.add_response("delete", "Delete Artwork")
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("keep")
    dialog.set_close_response("cancel")

    def on_response(_d, response):
        if response == "keep":
            on_choice(False)
        elif response == "delete":
            on_choice(True)
        elif response == "cancel" and on_cancel:
            on_cancel()

    dialog.connect("response", on_response)
    dialog.present(parent)


def handle_discard(parent: Gtk.Widget | None, desktop_id: str, app_name: str) -> None:
    def do_discard(delete_icon: bool = False):
        try:
            discard_override(desktop_id, delete_icon=delete_icon)
            _reload(parent)
            _toast(parent, f"Discarded overrides for {app_name}.")
        except OverrideError as exc:
            _toast(parent, str(exc))

    if can_delete_managed_icon(desktop_id):
        prompt_artwork_cleanup(
            parent,
            app_name,
            heading=f"Custom artwork for {app_name}",
            body=f"Glyph has a saved copy of custom artwork for “{app_name}”. Would you like to keep the image in icon storage for later reuse, or delete it?",
            on_choice=do_discard,
        )
    else:
        do_discard(delete_icon=False)


def prompt_external_modification(
    parent: Gtk.Widget | None,
    app_name: str,
    *,
    allow_overwrite: bool = True,
    on_overwrite=None,
    on_discard=None,
) -> None:
    """Prompt the user when an application launcher has been modified outside Glyph."""
    dialog = Adw.AlertDialog(
        heading="Launcher Modified Externally",
        body=(
            f"The desktop launcher for “{app_name}” was modified outside Glyph "
            "(for example, by an application update).\n\n"
            + (
                "You can overwrite the launcher with your new changes, or discard "
                "Glyph’s saved overrides to accept the external version."
                if allow_overwrite
                else "Would you like to discard Glyph’s saved overrides to synchronize with the current launcher?"
            )
        ),
    )
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("discard", "Discard Overrides")
    dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)

    if allow_overwrite:
        dialog.add_response("overwrite", "Overwrite")
        dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("overwrite")
    else:
        dialog.set_default_response("discard")

    dialog.set_close_response("cancel")

    def on_response(_d, response):
        if response == "overwrite" and on_overwrite:
            on_overwrite()
        elif response == "discard" and on_discard:
            on_discard()

    dialog.connect("response", on_response)
    dialog.present(parent)


def show_refresh_help(parent: Gtk.Widget | None) -> None:
    dialog = Adw.AlertDialog(
        heading="Refreshing icons",
        body=(
            "Glyph updates launcher icons and display names immediately on the dock and in application search.\n\n"
            "If your desktop's full application grid does not show the change right away (notably on some GNOME Shell versions), "
            "logging out and back in will force the shell to reload its cached textures."
        ),
    )
    dialog.add_response("ok", "OK")
    dialog.present(parent)


def confirm_revert_all_icons(parent: Gtk.Widget | None) -> None:
    try:
        state = load_state()
    except OverrideError as exc:
        _toast(parent, str(exc))
        return

    count = sum(bool(record.get("icon_path")) for record in state.values())
    if count == 0:
        _toast(parent, "No custom icons to revert.")
        return

    dialog = Adw.AlertDialog(
        heading="Revert all custom icons?",
        body=(
            f"This will restore original icons for {count} application{'s' if count != 1 else ''} "
            "while preserving custom names and pre-existing launchers."
        ),
    )
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("revert", "Revert All")
    dialog.set_response_appearance("revert", Adw.ResponseAppearance.DESTRUCTIVE)

    def on_response(_d, response):
        if response == "revert":
            try:
                res = revert_all_icons()
            except OverrideError as exc:
                _toast(parent, str(exc))
                return
            _reload(parent)
            msg = f"Restored original icons for {res.completed} application{'s' if res.completed != 1 else ''}."
            if res.errors:
                msg += f" ({len(res.errors)} failed)"
            _toast(parent, msg)

    dialog.connect("response", on_response)
    dialog.present(parent)


def confirm_revert_all_names(parent: Gtk.Widget | None) -> None:
    try:
        state = load_state()
    except OverrideError as exc:
        _toast(parent, str(exc))
        return

    count = sum(bool(record.get("custom_name")) for record in state.values())
    if count == 0:
        _toast(parent, "No custom names to revert.")
        return

    dialog = Adw.AlertDialog(
        heading="Revert all custom names?",
        body=(
            f"This will restore original names for {count} application{'s' if count != 1 else ''} "
            "while preserving custom icons and pre-existing launchers."
        ),
    )
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("revert", "Revert All")
    dialog.set_response_appearance("revert", Adw.ResponseAppearance.DESTRUCTIVE)

    def on_response(_d, response):
        if response == "revert":
            try:
                res = revert_all_names()
            except OverrideError as exc:
                _toast(parent, str(exc))
                return
            _reload(parent)
            msg = f"Restored original names for {res.completed} application{'s' if res.completed != 1 else ''}."
            if res.errors:
                msg += f" ({len(res.errors)} failed)"
            _toast(parent, msg)

    dialog.connect("response", on_response)
    dialog.present(parent)


def confirm_restore_all_stock(parent: Gtk.Widget | None) -> None:
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
            try:
                res = restore_all_to_stock()
            except OverrideError as exc:
                _toast(parent, str(exc))
                return
            _reload(parent)
            msg = f"Restored {res.completed} launcher{'s' if res.completed != 1 else ''} to system default."
            if res.errors:
                msg += f" ({len(res.errors)} failed)"
            _toast(parent, msg)

    dialog.connect("response", on_response)
    dialog.present(parent)


def export_overrides_dialog(parent: Gtk.Widget | None) -> None:
    try:
        state = load_state()
    except OverrideError as exc:
        _toast(parent, str(exc))
        return
    if not state:
        _toast(parent, "No customizations to export.")
        return

    exportable_count = sum(1 for r in state.values() if r.get('custom_name') or r.get('icon_path'))
    cmd_count = sum(1 for r in state.values() if r.get('custom_command'))

    if exportable_count == 0:
        _toast(parent, "No supported customizations to export. Backups cover custom names and icons, but exclude custom launch commands.")
        return

    def open_save_dialog():
        dialog = Gtk.FileDialog(title="Export Overrides Backup")
        dialog.set_initial_name("glyph-overrides-backup.tar.gz")

        def on_save_done(dlg: Gtk.FileDialog, result: Gio.AsyncResult):
            try:
                file = dlg.save_finish(result)
            except Exception:
                return
            if not file:
                return
            try:
                res = export_backup(Path(file.get_path()))
                if res.omitted:
                    _toast(
                        parent,
                        f"Exported {res.exported} customization{'s' if res.exported != 1 else ''} "
                        f"({res.omitted} command{'s' if res.omitted != 1 else ''} omitted)."
                    )
                else:
                    _toast(parent, f"Exported {res.exported} customization{'s' if res.exported != 1 else ''}.")
            except Exception as exc:
                _toast(parent, f"Export failed: {exc}")

        dialog.save(parent, None, on_save_done)

    explain_dialog = Adw.AlertDialog(
        heading="Export Customizations",
        body=(
            "Backup archives cover custom display names and icons.\n\n"
            "Custom launch commands are excluded so backup archives cannot execute arbitrary code when restored."
            + (
                f"\n\nNote: {cmd_count} custom launch command{'s' if cmd_count != 1 else ''} will be omitted from the backup."
                if cmd_count > 0
                else ""
            )
        ),
    )
    explain_dialog.add_response("cancel", "Cancel")
    explain_dialog.add_response("export", "Export…")
    explain_dialog.set_response_appearance("export", Adw.ResponseAppearance.SUGGESTED)
    explain_dialog.set_default_response("export")
    explain_dialog.set_close_response("cancel")

    def on_explain_response(_d, response):
        if response == "export":
            open_save_dialog()

    explain_dialog.connect("response", on_explain_response)
    explain_dialog.present(parent)


def handle_import_open_done(parent: Gtk.Widget | None, dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
    try:
        file = dlg.open_finish(result)
    except Exception:
        return
    if not file:
        return

    local_path = file.get_path()
    if not local_path:
        _toast(parent, "The selected backup is not available as a local file.")
        return
    source_path = Path(local_path)
    try:
        plan = preview_backup(source_path)
    except Exception as exc:
        _toast(parent, f"Invalid backup: {exc}")
        return

    def execute_import(replace_existing: bool):
        try:
            res = import_backup(source_path, replace_existing=replace_existing)
            _reload(parent)
            msg = f"Restored {res.completed} customization{'s' if res.completed != 1 else ''}."
            if res.skipped:
                msg += f" ({len(res.skipped)} skipped)"
            if res.errors:
                msg += f" ({len(res.errors)} failed)"
            _toast(parent, msg)
        except Exception as exc:
            _toast(parent, f"Import failed: {exc}")

    if plan.conflicts:
        confirm = Adw.AlertDialog(
            heading="Existing Customizations Found",
            body=(
                f"This backup contains {len(plan.conflicts)} application(s) that already have "
                "customizations. Would you like to overwrite them or keep your current settings?"
            ),
        )
        confirm.add_response("cancel", "Cancel")
        confirm.add_response("skip", "Keep Current")
        confirm.add_response("overwrite", "Overwrite")
        confirm.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_conflict_response(_d, resp):
            if resp == "overwrite":
                execute_import(replace_existing=True)
            elif resp == "skip":
                execute_import(replace_existing=False)

        confirm.connect("response", on_conflict_response)
        confirm.present(parent)
    else:
        execute_import(replace_existing=False)


def import_overrides_dialog(parent: Gtk.Widget | None) -> None:
    dialog = Gtk.FileDialog(title="Restore Overrides Backup")
    dialog.open(parent, None, lambda dlg, res: handle_import_open_done(parent, dlg, res))


def show_icon_picker(parent: Gtk.Widget | None, app: object, on_open_file_callback=None) -> None:
    themes = list_installed_themes()
    if not themes:
        if on_open_file_callback:
            on_open_file_callback()
        return

    dialog = Adw.Window(title="Change Icon")
    dialog.set_default_size(580, 520)
    dialog.set_modal(True)
    if parent:
        dialog.set_transient_for(parent)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()

    file_btn = Gtk.Button(label="Image File…")
    file_btn.set_tooltip_text("Choose a custom image file (PNG, SVG, WebP, JPG)")

    def on_choose_file(_btn):
        dialog.close()
        if on_open_file_callback:
            on_open_file_callback()

    file_btn.connect("clicked", on_choose_file)
    header.pack_end(file_btn)
    toolbar.add_top_bar(header)

    content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    content_box.set_margin_top(12)
    content_box.set_margin_bottom(12)
    content_box.set_margin_start(16)
    content_box.set_margin_end(16)

    controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    search_entry = Gtk.SearchEntry(placeholder_text="Search icon name…")
    search_entry.set_hexpand(True)
    controls_box.append(search_entry)

    theme_names = [t.name for t in themes]
    string_list = Gtk.StringList.new(theme_names)
    dropdown = Gtk.DropDown.new(string_list, None)
    dropdown.set_selected(0)
    controls_box.append(dropdown)
    content_box.append(controls_box)

    hint_lbl = Gtk.Label(label="Browse installed icon themes or search by icon name. Click to apply.")
    hint_lbl.add_css_class("caption")
    hint_lbl.add_css_class("dim-label")
    hint_lbl.set_halign(Gtk.Align.START)
    content_box.append(hint_lbl)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_vexpand(True)
    flowbox = Gtk.FlowBox()
    flowbox.set_valign(Gtk.Align.START)
    flowbox.set_max_children_per_line(8)
    flowbox.set_min_children_per_line(3)
    flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
    flowbox.set_row_spacing(8)
    flowbox.set_column_spacing(8)
    scrolled.set_child(flowbox)
    content_box.append(scrolled)

    toolbar.set_content(content_box)
    dialog.set_content(toolbar)

    def on_icon_clicked(icon: ThemeIcon):
        app_name = getattr(app, "name", "application")
        choice = Adw.AlertDialog(
            heading=f"Apply icon '{icon.name}'?",
            body=(
                f"Choose how to apply this icon for {app_name}:\n\n"
                "• Theme Icon: The application will follow changes when you switch desktop icon themes.\n\n"
                "• Fixed Artwork: Copies the icon file so it remains fixed even if the theme changes."
            ),
        )
        choice.add_response("cancel", "Cancel")
        choice.add_response("fixed", "Fixed Artwork")
        choice.add_response("theme", "Theme Icon")
        choice.set_response_appearance("theme", Adw.ResponseAppearance.SUGGESTED)
        choice.set_default_response("theme")
        choice.set_close_response("cancel")

        def on_choice_resp(_c, resp):
            if resp in ("theme", "fixed"):
                dialog.close()
                source_desktop = getattr(app, "filename", "") or getattr(app, "stock_filename", "")
                desktop_id = getattr(app, "desktop_id", "")

                def do_apply(force: bool = False, delete_icon: bool = False):
                    try:
                        if resp == "theme":
                            apply_theme_icon(desktop_id, source_desktop, icon.name, force=force, delete_icon=delete_icon)
                            _toast(parent, f"Theme icon '{icon.name}' applied.")
                        else:
                            apply_icon(desktop_id, source_desktop, icon.file_path, force=force, delete_icon=delete_icon)
                            _toast(parent, f"Fixed icon '{icon.name}' applied.")
                        _reload(parent)
                    except LauncherModifiedExternallyError:
                        prompt_external_modification(
                            parent,
                            app_name,
                            allow_overwrite=True,
                            on_overwrite=lambda: do_apply(force=True, delete_icon=delete_icon),
                            on_discard=lambda: handle_discard(parent, desktop_id, app_name),
                        )
                    except OverrideError as exc:
                        _toast(parent, str(exc))

                if can_delete_managed_icon(desktop_id):
                    prompt_artwork_cleanup(
                        parent,
                        app_name,
                        heading=f"Replace artwork for {app_name}?",
                        body="Glyph has a saved copy of custom artwork for this application. Would you like to keep the image in icon storage for later reuse, or delete it?",
                        on_choice=lambda delete: do_apply(force=False, delete_icon=delete),
                    )
                else:
                    do_apply(force=False, delete_icon=False)

        choice.connect("response", on_choice_resp)
        choice.present(dialog)

    def refresh_icons(*_args):
        while child := flowbox.get_first_child():
            flowbox.remove(child)

        idx = dropdown.get_selected()
        if idx >= len(themes):
            return
        current_theme = themes[idx]
        query = search_entry.get_text()
        icons = search_theme_icons(current_theme.theme_id, query)

        for icon in icons:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.set_halign(Gtk.Align.CENTER)
            box.set_size_request(64, 64)
            img = Gtk.Image.new_from_icon_name(icon.name)
            img.set_pixel_size(36)
            box.append(img)

            lbl = Gtk.Label(label=icon.name)
            lbl.set_max_width_chars(8)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.add_css_class("caption")
            box.append(lbl)

            btn.set_child(box)
            btn.set_tooltip_text(f"{icon.name} ({icon.context})")
            btn.connect("clicked", lambda _b, ic=icon: on_icon_clicked(ic))
            flowbox.append(btn)

    search_entry.connect("search-changed", refresh_icons)
    dropdown.connect("notify::selected", refresh_icons)
    refresh_icons()
    dialog.present()
