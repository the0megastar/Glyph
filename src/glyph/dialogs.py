from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from glyph.overrides import (
    OverrideError,
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


def show_refresh_help(parent: Gtk.Widget | None) -> None:
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
            res = revert_all_icons()
            _reload(parent)
            msg = f"Restored original icons for {res.completed} application{'s' if res.completed != 1 else ''}. Log out to refresh grid."
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
            res = revert_all_names()
            _reload(parent)
            msg = f"Restored original names for {res.completed} application{'s' if res.completed != 1 else ''}. Log out to refresh grid."
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
            n = export_backup(Path(file.get_path()))
            _toast(parent, f"Exported {n} customization{'s' if n != 1 else ''}.")
        except Exception as exc:
            _toast(parent, f"Export failed: {exc}")

    dialog.save(parent, None, on_save_done)


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
            msg += " Log out to refresh grid."
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

