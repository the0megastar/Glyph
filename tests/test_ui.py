import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, Gtk

from glyph import window
from glyph import main
from glyph import dialogs
from glyph.overrides import OverrideError
import glyph.overrides as ov


class TestUI(unittest.TestCase):
    def test_bulk_reset_execution_error_is_shown(self):
        for confirm, operation, record in [
                (dialogs.confirm_revert_all_icons, 'revert_all_icons', {'icon_path': 'icon'}),
                (dialogs.confirm_revert_all_names, 'revert_all_names', {'custom_name': 'name'})]:
            with self.subTest(operation=operation):
                alert = MagicMock()
                parent = MagicMock()
                with patch.object(dialogs.Adw, 'AlertDialog', return_value=alert), patch.object(
                        dialogs, 'load_state', return_value={'demo.desktop': record}), patch.object(
                        dialogs, operation, side_effect=OverrideError('Cannot read state')) as run:
                    confirm(parent)
                    callback = alert.connect.call_args.args[1]
                    callback(alert, 'cancel')
                    run.assert_not_called()
                    callback(alert, 'revert')
                parent._toast.assert_called_once_with('Cannot read state')
                parent.reload.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="glyph-ui-test-")
        self.root = Path(self.tmp.name)
        self.app_dir = self.root / "applications"
        self.data_dir = self.root / "glyph"
        self.icons_dir = self.data_dir / "icons"
        self.state_file = self.data_dir / "overrides.json"
        self.patchers = [
            patch.object(ov, "APPLICATIONS_DIR", self.app_dir),
            patch.object(ov, "DATA_DIR", self.data_dir),
            patch.object(ov, "ICONS_DIR", self.icons_dir),
            patch.object(ov, "STATE_FILE", self.state_file),
            patch.object(ov, "refresh_desktop_database"),
        ]
        for path_patcher in self.patchers:
            path_patcher.start()
        ov._ensure_dirs()

    def tearDown(self):
        for path_patcher in reversed(self.patchers):
            path_patcher.stop()
        self.tmp.cleanup()

    def test_ui_reload_handles_damaged_state(self):
        toasts = []
        stub = SimpleNamespace(
            _apps=[],
            _detail_id=None,
            _toast=lambda msg: toasts.append(msg),
            _update_filter_menu=lambda: None,
            _rebuild_list=lambda: None,
        )
        self.state_file.write_text("{ corrupt json ...", encoding="utf-8")
        # Should not raise exception
        window.GlyphWindow.reload(stub)
        self.assertTrue(len(toasts) > 0)
        self.assertIn("overrides.json", toasts[0])

    def test_flatpak_launch_capability_disabled(self):
        badge_box = MagicMock()
        badge_box.get_first_child.return_value = None
        stub = SimpleNamespace(
            _detail_title=MagicMock(),
            _name_reset_btn=MagicMock(),
            _badge_box=badge_box,
            _revert_button=MagicMock(),
            _stock_button=MagicMock(),
            _bottom_box=MagicMock(),
            _desktop_file_row=MagicMock(),
            _open_desktop_file=MagicMock(),
            _app_folder_row=MagicMock(),
            _command_row=MagicMock(),
            _launch_btn=MagicMock(),
            _detail_page=MagicMock(),
        )
        app = SimpleNamespace(
            name="Host App",
            desktop_id="host.desktop",
            source="System",
            command="/usr/bin/host-app",
            filename="/usr/share/applications/host.desktop",
            stock_filename="",
            app_folder="",
            custom_icon=False,
            custom_name=False,
            has_stock=False,
            gicon=None,
        )
        with patch("glyph.window.in_flatpak", return_value=True), \
             patch("glyph.launcher.can_launch_apps", return_value=False), \
             patch("glyph.window.Gtk.Label"):
            window.GlyphWindow._fill_detail(stub, app)
            # When launcher capability is unavailable in flatpak, launch should be disabled and show informative tooltip
            stub._launch_btn.set_sensitive.assert_called_with(False)
            stub._command_row.set_tooltip_text.assert_called_with(
                "Launching external applications is not supported from within Flatpak sandbox"
            )

    def test_flatpak_launch_capability_enabled(self):
        badge_box = MagicMock()
        badge_box.get_first_child.return_value = None
        stub = SimpleNamespace(
            _detail_title=MagicMock(),
            _name_reset_btn=MagicMock(),
            _badge_box=badge_box,
            _source_row=MagicMock(),
            _version_row=MagicMock(),
            _appimage_row=MagicMock(),
            _command_reset_btn=MagicMock(),
            _guidance_row=MagicMock(),
            _open_software_btn=MagicMock(),
            _revert_button=MagicMock(),
            _stock_button=MagicMock(),
            _unregister_appimage_btn=MagicMock(),
            _bottom_box=MagicMock(),
            _desktop_file_row=MagicMock(),
            _open_desktop_file=MagicMock(),
            _app_folder_row=MagicMock(),
            _command_row=MagicMock(),
            _launch_btn=MagicMock(),
            _detail_page=MagicMock(),
        )
        app = SimpleNamespace(
            name="Host App",
            desktop_id="host.desktop",
            source="System",
            command="/usr/bin/host-app",
            filename="/usr/share/applications/host.desktop",
            stock_filename="",
            app_folder="",
            custom_icon=False,
            custom_name=False,
            has_stock=False,
            gicon=None,
        )
        with patch("glyph.window.in_flatpak", return_value=True), \
             patch("glyph.launcher.can_launch_apps", return_value=True), \
             patch("glyph.window.Gtk.Label"):
            window.GlyphWindow._fill_detail(stub, app)
            stub._launch_btn.set_sensitive.assert_called_with(True)
            stub._command_row.set_tooltip_text.assert_called_with(None)

    def test_primary_menu_contains_reset_submenu_and_preferences(self):
        menu_model = window._primary_menu_model()
        self.assertIsNotNone(menu_model)

        def contains_action(model, expected):
            for index in range(model.get_n_items()):
                action = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_ACTION)
                if action and action.get_string() == expected:
                    return True
                for link_name in (Gio.MENU_LINK_SECTION, Gio.MENU_LINK_SUBMENU):
                    child = model.get_item_link(index, link_name)
                    if child and contains_action(child, expected):
                        return True
            return False

        self.assertTrue(contains_action(menu_model, "app.revert-all-names"))
        self.assertTrue(contains_action(menu_model, "app.preferences"))

        reset_section = menu_model.get_item_link(1, Gio.MENU_LINK_SECTION)
        self.assertIsNotNone(reset_section)
        self.assertIsNotNone(reset_section.get_item_link(0, Gio.MENU_LINK_SUBMENU))

    def test_import_rejects_non_local_backup(self):
        toasts = []
        win = SimpleNamespace(_toast=toasts.append)
        app = SimpleNamespace(props=SimpleNamespace(active_window=win))
        dialog = MagicMock()
        dialog.open_finish.return_value.get_path.return_value = None

        main.GlyphApplication._on_import_open_done(app, dialog, MagicMock())

        self.assertEqual(toasts, ["The selected backup is not available as a local file."])

    def test_open_app_folder_launches_directory(self):
        launcher = MagicMock()
        stub = SimpleNamespace(
            _detail_id="demo.desktop",
            _find=lambda _desktop_id: SimpleNamespace(app_folder="/usr/bin"),
            _on_open_folder_done=MagicMock(),
        )
        with patch.object(window.Gtk.FileLauncher, "new", return_value=launcher):
            window.GlyphWindow._on_open_app_folder(stub, MagicMock())
        launcher.launch.assert_called_once_with(stub, None, stub._on_open_folder_done)


    def test_primary_menu_contains_add_appimage(self):
        menu_model = window._primary_menu_model()
        self.assertIsNotNone(menu_model)

        def contains_action(model, expected):
            for index in range(model.get_n_items()):
                action = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_ACTION)
                if action and action.get_string() == expected:
                    return True
                for link_name in (Gio.MENU_LINK_SECTION, Gio.MENU_LINK_SUBMENU):
                    child = model.get_item_link(index, link_name)
                    if child and contains_action(child, expected):
                        return True
            return False

        self.assertTrue(contains_action(menu_model, "win.add-appimage"))

    def test_command_reset_invokes_revert(self):
        toasts = []
        stub = SimpleNamespace(
            _detail_id="demo.desktop",
            reload=MagicMock(),
            _toast=toasts.append,
        )
        with patch("glyph.window.revert_command") as mock_revert:
            window.GlyphWindow._on_command_reset(stub, MagicMock())
            mock_revert.assert_called_once_with("demo.desktop")
            stub.reload.assert_called_once()
            self.assertEqual(toasts, ["Original command restored."])

    def test_on_launch_delegates_to_launcher(self):
        toasts = []
        app = SimpleNamespace(name="Demo App", desktop_id="demo.desktop")
        stub = SimpleNamespace(
            _detail_id="demo.desktop",
            _find=lambda _desktop_id: app,
            _toast=toasts.append,
        )
        with patch("glyph.launcher.launch") as mock_launch:
            window.GlyphWindow._on_launch(stub, MagicMock())
            mock_launch.assert_called_once_with(app)
            self.assertEqual(toasts, ["Launched Demo App."])

    def test_unregister_appimage_flow(self):
        toasts = []
        app = SimpleNamespace(name="Sample App", desktop_id="sample.desktop")
        dialog = MagicMock()
        stub = SimpleNamespace(
            _detail_id="sample.desktop",
            _find=lambda _desktop_id: app,
            _toast=toasts.append,
            reload=MagicMock(),
            nav=MagicMock(),
            present=MagicMock(),
        )
        with patch("glyph.window.Adw.AlertDialog", return_value=dialog), \
             patch("glyph.appimage.unregister_appimage") as mock_unreg:
            window.GlyphWindow._on_unregister_appimage(stub, MagicMock())
            dialog.connect.assert_called_once()
            cb = dialog.connect.call_args.args[1]
            cb(dialog, "unregister")
            mock_unreg.assert_called_once_with("sample.desktop")
            stub.nav.pop.assert_called_once()
            self.assertIsNone(stub._detail_id)
            stub.reload.assert_called_once()
            self.assertIn("Unregistered launcher for Sample App", toasts[0])

    def test_copy_terminal_cmd(self):
        toasts = []
        clipboard = MagicMock()
        display = MagicMock()
        display.get_clipboard.return_value = clipboard
        stub = SimpleNamespace(
            _current_terminal_cmd="sudo dnf upgrade --refresh",
            _toast=toasts.append,
        )
        with patch("glyph.window.Gdk.Display.get_default", return_value=display):
            window.GlyphWindow._on_copy_terminal_cmd(stub, MagicMock())
            clipboard.set.assert_called_once_with("sudo dnf upgrade --refresh")
            self.assertEqual(toasts, ["Command copied to clipboard."])


    def test_on_open_software_delegates_to_guidance(self):
        app = SimpleNamespace(name="Workbench", desktop_id="re.sonny.Workbench.desktop", flatpak_id="re.sonny.Workbench")
        stub = SimpleNamespace(
            _detail_id="re.sonny.Workbench.desktop",
            _find=lambda _desktop_id: app,
        )
        with patch("glyph.guidance.open_software_tool") as mock_open:
            window.GlyphWindow._on_open_software(stub)
            mock_open.assert_called_once_with("re.sonny.Workbench")

    def test_on_open_software_fallback_strips_desktop_suffix(self):
        app = SimpleNamespace(name="Calculator", desktop_id="org.gnome.Calculator.desktop")
        stub = SimpleNamespace(
            _detail_id="org.gnome.Calculator.desktop",
            _find=lambda _desktop_id: app,
        )
        with patch("glyph.guidance.open_software_tool") as mock_open:
            window.GlyphWindow._on_open_software(stub)
            mock_open.assert_called_once_with("org.gnome.Calculator")

    def test_register_candidate_flow(self):
        cand = SimpleNamespace(path=Path("/home/user/Applications/Test.AppImage"), display_name="Test")
        stub = SimpleNamespace(
            _toast=MagicMock(),
            _show_appimage_registration_preview=MagicMock(),
        )
        fake_plan = MagicMock()
        with patch("glyph.appimage.plan_appimage_registration", return_value=fake_plan) as mock_plan:
            window.GlyphWindow._on_register_candidate(stub, cand)
            mock_plan.assert_called_once_with(cand.path)
            stub._show_appimage_registration_preview.assert_called_once_with(fake_plan, open_detail=False)

    def test_register_candidate_error_toast(self):
        cand = SimpleNamespace(path=Path("/home/user/Applications/Broken.AppImage"), display_name="Broken")
        toasts = []
        stub = SimpleNamespace(
            _toast=toasts.append,
            _show_appimage_registration_preview=MagicMock(),
        )
        from glyph.appimage import AppImageError
        with patch("glyph.appimage.plan_appimage_registration", side_effect=AppImageError("Not readable")):
            window.GlyphWindow._on_register_candidate(stub, cand)
            self.assertEqual(toasts, ["Cannot register AppImage: Not readable"])
            stub._show_appimage_registration_preview.assert_not_called()

    def test_prompt_external_modification_responses(self):
        alert = MagicMock()
        parent = MagicMock()
        mock_overwrite = MagicMock()
        mock_discard = MagicMock()

        with patch.object(dialogs.Adw, "AlertDialog", return_value=alert):
            dialogs.prompt_external_modification(
                parent,
                "Grok Bot",
                allow_overwrite=True,
                on_overwrite=mock_overwrite,
                on_discard=mock_discard,
            )
            cb = alert.connect.call_args.args[1]

            # Overwrite response
            cb(alert, "overwrite")
            mock_overwrite.assert_called_once()
            mock_discard.assert_not_called()

            # Discard response
            cb(alert, "discard")
            mock_discard.assert_called_once()

    def test_apply_icon_path_triggers_prompt_on_external_modification(self):
        from glyph.overrides import LauncherModifiedExternallyError
        app = SimpleNamespace(name="Grok Bot", desktop_id="grok-bot.desktop", filename="/path/grok.desktop", stock_filename=None)
        stub = SimpleNamespace(
            _toast=MagicMock(),
            reload=MagicMock(),
            _discard_overrides=MagicMock(),
            _apply_icon_path=lambda app, path, force=False: window.GlyphWindow._apply_icon_path(stub, app, path, force=force),
        )
        with patch("glyph.window.apply_icon", side_effect=LauncherModifiedExternallyError("Modified", "grok-bot.desktop")), \
             patch("glyph.dialogs.prompt_external_modification") as mock_prompt:
            result = window.GlyphWindow._apply_icon_path(stub, app, "/new/icon.png")
            self.assertFalse(result)
            mock_prompt.assert_called_once()
            self.assertEqual(mock_prompt.call_args.args[1], "Grok Bot")
            self.assertTrue(mock_prompt.call_args.kwargs["allow_overwrite"])

    def test_on_revert_triggers_prompt_on_external_modification(self):
        from glyph.overrides import LauncherModifiedExternallyError
        app = SimpleNamespace(name="Grok Bot", desktop_id="grok-bot.desktop")
        stub = SimpleNamespace(
            _detail_id="grok-bot.desktop",
            _find=lambda _id: app,
            _toast=MagicMock(),
            reload=MagicMock(),
            _discard_overrides=MagicMock(),
        )
        with patch("glyph.window.revert_icon", side_effect=LauncherModifiedExternallyError("Modified", "grok-bot.desktop")), \
             patch("glyph.dialogs.prompt_external_modification") as mock_prompt:
            window.GlyphWindow._on_revert(stub, MagicMock())
            mock_prompt.assert_called_once()
            self.assertEqual(mock_prompt.call_args.args[1], "Grok Bot")
            self.assertFalse(mock_prompt.call_args.kwargs["allow_overwrite"])

    def test_prompt_artwork_cleanup_responses(self):
        alert = MagicMock()
        parent = MagicMock()
        mock_choice = MagicMock()
        mock_cancel = MagicMock()

        with patch.object(dialogs.Adw, "AlertDialog", return_value=alert):
            dialogs.prompt_artwork_cleanup(
                parent,
                "Test App",
                heading="Artwork Heading",
                body="Artwork Body",
                on_choice=mock_choice,
                on_cancel=mock_cancel,
            )
            cb = alert.connect.call_args.args[1]

            # Choose Keep (delete_icon=False)
            cb(alert, "keep")
            mock_choice.assert_called_once_with(False)
            mock_choice.reset_mock()

            # Choose Delete (delete_icon=True)
            cb(alert, "delete")
            mock_choice.assert_called_once_with(True)
            mock_choice.reset_mock()

            # Choose Cancel
            cb(alert, "cancel")
            mock_choice.assert_not_called()
            mock_cancel.assert_called_once()

    def test_export_overrides_dialog_only_commands_shows_toast_and_no_file_dialog(self):
        parent = MagicMock()
        state = {
            "cmd.desktop": {
                "custom_command": "some-cmd",
            }
        }
        with patch("glyph.dialogs.load_state", return_value=state), \
             patch.object(dialogs.Adw, "AlertDialog") as mock_alert, \
             patch.object(dialogs.Gtk, "FileDialog") as mock_file_dialog:
            dialogs.export_overrides_dialog(parent)
            parent._toast.assert_called_once()
            toast_msg = parent._toast.call_args.args[0]
            self.assertIn("No supported customizations to export", toast_msg)
            self.assertIn("exclude custom launch commands", toast_msg)
            mock_alert.assert_not_called()
            mock_file_dialog.assert_not_called()

    def test_export_overrides_dialog_explains_and_reports_omitted_commands(self):
        parent = MagicMock()
        state = {
            "app1.desktop": {"custom_name": "New Name"},
            "cmd.desktop": {"custom_command": "some-cmd"},
        }
        alert = MagicMock()
        file_dialog = MagicMock()

        from glyph.backups import ExportResult
        fake_result = ExportResult(1, omitted=1, command_only=1, mixed=0)

        with patch("glyph.dialogs.load_state", return_value=state), \
             patch.object(dialogs.Adw, "AlertDialog", return_value=alert) as mock_alert_cls, \
             patch.object(dialogs.Gtk, "FileDialog", return_value=file_dialog), \
             patch("glyph.dialogs.export_backup", return_value=fake_result):

            dialogs.export_overrides_dialog(parent)
            alert.present.assert_called_once_with(parent)

            # Confirm explanation dialog mentions omitted command
            self.assertIn("1 custom launch command will be omitted", mock_alert_cls.call_args.kwargs["body"])

            # User confirms "export"
            explain_cb = alert.connect.call_args.args[1]
            explain_cb(alert, "export")

            # FileDialog was saved
            file_dialog.save.assert_called_once()
            on_save_done = file_dialog.save.call_args.args[2]

            # Simulate save completion
            fake_file = MagicMock()
            fake_file.get_path.return_value = "/path/to/backup.tar.gz"
            file_dialog.save_finish.return_value = fake_file

            on_save_done(file_dialog, MagicMock())
            parent._toast.assert_called_once_with("Exported 1 customization (1 command omitted).")

    def test_window_on_revert_with_and_without_managed_icon(self):
        app = SimpleNamespace(name="Revert App", desktop_id="revert.desktop")
        stub = SimpleNamespace(
            _detail_id="revert.desktop",
            _find=lambda _id: app,
            _toast=MagicMock(),
            reload=MagicMock(),
            _discard_overrides=MagicMock(),
        )

        # Case 1: can_delete_managed_icon is True -> prompts keep/delete
        with patch("glyph.window.can_delete_managed_icon", return_value=True), \
             patch("glyph.dialogs.prompt_artwork_cleanup") as mock_prompt, \
             patch("glyph.window.revert_icon") as mock_revert:
            window.GlyphWindow._on_revert(stub, MagicMock())
            mock_prompt.assert_called_once()
            on_choice = mock_prompt.call_args.kwargs["on_choice"]

            # User chooses delete
            on_choice(delete_icon=True)
            mock_revert.assert_called_once_with("revert.desktop", delete_icon=True)

        # Case 2: can_delete_managed_icon is False -> reverts directly with delete_icon=False
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch("glyph.dialogs.prompt_artwork_cleanup") as mock_prompt, \
             patch("glyph.window.revert_icon") as mock_revert:
            window.GlyphWindow._on_revert(stub, MagicMock())
            mock_prompt.assert_not_called()
            mock_revert.assert_called_once_with("revert.desktop", delete_icon=False)

    def test_window_on_restore_stock_with_and_without_managed_icon(self):
        app = SimpleNamespace(name="Stock App", desktop_id="stock.desktop")
        stub = SimpleNamespace(
            _detail_id="stock.desktop",
            _find=lambda _id: app,
            _toast=MagicMock(),
            reload=MagicMock(),
            _discard_overrides=MagicMock(),
        )

        # Case 1: can_delete_managed_icon is True -> prompts keep/delete dialog
        alert1 = MagicMock()
        with patch("glyph.window.can_delete_managed_icon", return_value=True), \
             patch.object(dialogs.Adw, "AlertDialog", return_value=alert1), \
             patch("glyph.window.restore_stock_launcher") as mock_restore:
            window.GlyphWindow._on_restore_stock(stub, MagicMock())
            alert1.present.assert_called_once()
            cb = alert1.connect.call_args.args[1]

            # Choose keep
            cb(alert1, "keep")
            mock_restore.assert_called_once_with("stock.desktop", force=False, delete_icon=False)

        # Case 2: can_delete_managed_icon is False -> standard restore confirmation dialog
        alert2 = MagicMock()
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch.object(dialogs.Adw, "AlertDialog", return_value=alert2), \
             patch("glyph.window.restore_stock_launcher") as mock_restore:
            window.GlyphWindow._on_restore_stock(stub, MagicMock())
            alert2.present.assert_called_once()
            cb = alert2.connect.call_args.args[1]

            # Choose restore
            cb(alert2, "restore")
            mock_restore.assert_called_once_with("stock.desktop", force=False, delete_icon=False)


if __name__ == "__main__":
    unittest.main()


