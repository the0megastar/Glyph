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

    def test_confirm_revert_all_icons_artwork_prompt(self):
        parent = MagicMock()
        state = {"app1.desktop": {"icon_path": "/path/1.png"}}

        # Case 1: eligible_count > 0 -> prompts keep/delete artwork
        with patch("glyph.dialogs.load_state", return_value=state), \
             patch("glyph.dialogs.can_delete_managed_icon", return_value=True), \
             patch("glyph.dialogs.prompt_artwork_cleanup") as mock_prompt, \
             patch("glyph.dialogs.revert_all_icons") as mock_revert:
            dialogs.confirm_revert_all_icons(parent)
            mock_prompt.assert_called_once()
            on_choice = mock_prompt.call_args.kwargs["on_choice"]

            on_choice(delete_icon=True)
            mock_revert.assert_called_once_with(delete_icon=True)

        # Case 2: eligible_count == 0 -> standard dialog
        alert = MagicMock()
        with patch("glyph.dialogs.load_state", return_value=state), \
             patch("glyph.dialogs.can_delete_managed_icon", return_value=False), \
             patch.object(dialogs.Adw, "AlertDialog", return_value=alert), \
             patch("glyph.dialogs.revert_all_icons") as mock_revert:
            dialogs.confirm_revert_all_icons(parent)
            alert.present.assert_called_once_with(parent)
            cb = alert.connect.call_args.args[1]
            cb(alert, "revert")
            mock_revert.assert_called_once_with(delete_icon=False)

    def test_confirm_restore_all_stock_artwork_prompt(self):
        parent = MagicMock()
        self.app_dir.mkdir(parents=True, exist_ok=True)
        local_path = self.app_dir / "stock.desktop"
        local_path.write_text("[Desktop Entry]\nType=Application\nName=Stock\n", encoding="utf-8")
        state = {
            "stock.desktop": {
                "icon_path": "/path/stock.png",
                "created_local": True,
                "local_desktop": str(local_path),
            }
        }

        # Case 1: eligible_count > 0 -> prompts keep/delete artwork
        with patch("glyph.dialogs.load_state", return_value=state), \
             patch("glyph.paths.desktop_index", return_value={"stock.desktop": local_path}), \
             patch("glyph.paths.find_stock", return_value="/stock/path"), \
             patch("glyph.overrides.APPLICATIONS_DIR", self.app_dir), \
             patch("glyph.dialogs.can_delete_managed_icon", return_value=True), \
             patch("glyph.dialogs.prompt_artwork_cleanup") as mock_prompt, \
             patch("glyph.dialogs.restore_all_to_stock") as mock_restore:
            dialogs.confirm_restore_all_stock(parent)
            mock_prompt.assert_called_once()
            on_choice = mock_prompt.call_args.kwargs["on_choice"]

            on_choice(delete_icon=False)
            mock_restore.assert_called_once_with(delete_icon=False)

        # Case 2: eligible_count == 0 -> standard dialog
        alert = MagicMock()
        with patch("glyph.dialogs.load_state", return_value=state), \
             patch("glyph.paths.desktop_index", return_value={"stock.desktop": local_path}), \
             patch("glyph.paths.find_stock", return_value="/stock/path"), \
             patch("glyph.overrides.APPLICATIONS_DIR", self.app_dir), \
             patch("glyph.dialogs.can_delete_managed_icon", return_value=False), \
             patch.object(dialogs.Adw, "AlertDialog", return_value=alert), \
             patch("glyph.dialogs.restore_all_to_stock") as mock_restore:
            dialogs.confirm_restore_all_stock(parent)
            alert.present.assert_called_once_with(parent)
            cb = alert.connect.call_args.args[1]
            cb(alert, "restore")
            mock_restore.assert_called_once_with(delete_icon=False)

    def test_handle_import_open_done_artwork_prompt_when_replacing_icons(self):
        parent = MagicMock()
        dlg = MagicMock()
        fake_file = MagicMock()
        fake_file.get_path.return_value = "/backup.tar.gz"
        dlg.open_finish.return_value = fake_file

        # Plan with conflict that has an icon in backup
        plan = SimpleNamespace(
            entries={"app.desktop": {"icon": "icons/new.png"}},
            conflicts=["app.desktop"],
            missing=[],
            assets={"icons/new.png": b"data"},
            sources={"app.desktop": "/path"},
        )
        current_state = {"app.desktop": {"icon_path": "/old.png"}}

        confirm_alert = MagicMock()
        with patch("glyph.dialogs.preview_backup", return_value=plan), \
             patch("glyph.dialogs.load_state", return_value=current_state), \
             patch("glyph.dialogs.can_delete_managed_icon", return_value=True), \
             patch.object(dialogs.Adw, "AlertDialog", return_value=confirm_alert), \
             patch("glyph.dialogs.prompt_artwork_cleanup") as mock_prompt, \
             patch("glyph.dialogs.import_backup", return_value=ov.BatchResult()) as mock_import:

            dialogs.handle_import_open_done(parent, dlg, MagicMock())
            confirm_alert.present.assert_called_once_with(parent)

            # Choose overwrite in conflict dialog -> should trigger prompt_artwork_cleanup
            conflict_cb = confirm_alert.connect.call_args.args[1]
            conflict_cb(confirm_alert, "overwrite")

            mock_prompt.assert_called_once()
            on_choice = mock_prompt.call_args.kwargs["on_choice"]

            # User chooses Keep (delete=False)
            on_choice(False)
            mock_import.assert_called_once_with(Path("/backup.tar.gz"), replace_existing=True, delete_icon=False)

    def test_window_on_unregister_appimage_toast_messages(self):
        app = SimpleNamespace(name="Toast App", desktop_id="toast.desktop")
        toasts = []
        stub = SimpleNamespace(
            _toast=lambda msg: toasts.append(msg),
            reload=MagicMock(),
            nav=MagicMock(),
            _detail_id="toast.desktop",
            _find=lambda did: app,
        )

        # 1. icon_kept -> "Artwork and binary file preserved."
        stub._detail_id = "toast.desktop"
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch("glyph.window.Adw.AlertDialog") as mock_alert, \
             patch("glyph.appimage.unregister_appimage", return_value={"icon_kept": True}):
            alert_instance = MagicMock()
            mock_alert.return_value = alert_instance
            window.GlyphWindow._on_unregister_appimage(stub, MagicMock())
            cb = alert_instance.connect.call_args.args[1]
            cb(alert_instance, "unregister")
            self.assertEqual(toasts[-1], "Unregistered launcher for Toast App. Artwork and binary file preserved.")

        # 2. icon_skipped with modified -> "Modified artwork and binary file preserved."
        stub._detail_id = "toast.desktop"
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch("glyph.window.Adw.AlertDialog") as mock_alert, \
             patch("glyph.appimage.unregister_appimage", return_value={"icon_skipped": True, "icon_skip_reason": "modified"}):
            alert_instance = MagicMock()
            mock_alert.return_value = alert_instance
            window.GlyphWindow._on_unregister_appimage(stub, MagicMock())
            cb = alert_instance.connect.call_args.args[1]
            cb(alert_instance, "unregister")
            self.assertEqual(toasts[-1], "Unregistered launcher for Toast App. Modified artwork and binary file preserved.")

        # 3. icon_skipped with shared -> "Artwork and binary file preserved."
        stub._detail_id = "toast.desktop"
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch("glyph.window.Adw.AlertDialog") as mock_alert, \
             patch("glyph.appimage.unregister_appimage", return_value={"icon_skipped": True, "icon_skip_reason": "shared"}):
            alert_instance = MagicMock()
            mock_alert.return_value = alert_instance
            window.GlyphWindow._on_unregister_appimage(stub, MagicMock())
            cb = alert_instance.connect.call_args.args[1]
            cb(alert_instance, "unregister")
            self.assertEqual(toasts[-1], "Unregistered launcher for Toast App. Artwork and binary file preserved.")

        # 4. cleanly deleted -> "Binary file preserved."
        stub._detail_id = "toast.desktop"
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch("glyph.window.Adw.AlertDialog") as mock_alert, \
             patch("glyph.appimage.unregister_appimage", return_value={"icon_deleted": True}):
            alert_instance = MagicMock()
            mock_alert.return_value = alert_instance
            window.GlyphWindow._on_unregister_appimage(stub, MagicMock())
            cb = alert_instance.connect.call_args.args[1]
            cb(alert_instance, "unregister")
            self.assertEqual(toasts[-1], "Unregistered launcher for Toast App. Binary file preserved.")

    def test_confirm_revert_all_icons_with_theme_icon_and_image(self):
        parent = MagicMock()
        state = {
            "app_img.desktop": {"icon_path": "/path/1.png"},
            "app_theme.desktop": {"theme_icon": "custom-theme"},
        }

        with patch("glyph.dialogs.load_state", return_value=state), \
             patch("glyph.dialogs.can_delete_managed_icon", side_effect=lambda k, s: k == "app_img.desktop"), \
             patch("glyph.dialogs.prompt_artwork_cleanup") as mock_prompt, \
             patch("glyph.dialogs.revert_all_icons", return_value=ov.BatchResult(completed=2)) as mock_revert:
            dialogs.confirm_revert_all_icons(parent)
            mock_prompt.assert_called_once()
            kwargs = mock_prompt.call_args.kwargs
            # Body should mention 2 applications and 1 saved custom image
            self.assertIn("2 applications", kwargs["body"])
            self.assertIn("1 saved custom image", kwargs["body"])

            on_choice = kwargs["on_choice"]
            on_choice(delete_icon=True)
            mock_revert.assert_called_once_with(delete_icon=True)
            parent._toast.assert_called_once_with("Restored original icons for 2 applications.")

    def test_confirm_restore_all_stock_counts_only_glyph_created_launchers(self):
        parent = MagicMock()
        self.app_dir.mkdir(parents=True, exist_ok=True)
        glyph_local = self.app_dir / "glyph_app.desktop"
        glyph_local.write_text("[Desktop Entry]\nType=Application\nName=GlyphApp\n", encoding="utf-8")
        user_local = self.app_dir / "user_app.desktop"
        user_local.write_text("[Desktop Entry]\nType=Application\nName=UserApp\n", encoding="utf-8")

        state = {
            "glyph_app.desktop": {
                "icon_path": "/path/img1.png",
                "created_local": True,
                "local_desktop": str(glyph_local),
            },
            "user_app.desktop": {
                "icon_path": "/path/img2.png",
                "created_local": False,
                "local_desktop": str(user_local),
            },
        }

        with patch("glyph.dialogs.load_state", return_value=state), \
             patch("glyph.paths.desktop_index", return_value={"glyph_app.desktop": glyph_local, "user_app.desktop": user_local}), \
             patch("glyph.paths.find_stock", return_value="/stock/path"), \
             patch("glyph.overrides.APPLICATIONS_DIR", self.app_dir), \
             patch("glyph.dialogs.can_delete_managed_icon", return_value=True), \
             patch("glyph.dialogs.prompt_artwork_cleanup") as mock_prompt, \
             patch("glyph.dialogs.restore_all_to_stock", return_value=ov.BatchResult(completed=1, errors=[("user_app.desktop", "Pre-existing")])) as mock_restore:
            dialogs.confirm_restore_all_stock(parent)
            mock_prompt.assert_called_once()
            kwargs = mock_prompt.call_args.kwargs
            # Only the Glyph-created launcher should be counted in artwork cleanup
            self.assertIn("1 saved custom image", kwargs["body"])

            on_choice = kwargs["on_choice"]
            on_choice(delete_icon=True)
            mock_restore.assert_called_once_with(delete_icon=True)
            parent._toast.assert_called_once_with("Restored 1 launcher to system default. (1 failed)")

    def test_discard_override_toast_messages(self):
        app = SimpleNamespace(name="Discard App", desktop_id="discard.desktop")
        toasts = []
        win_stub = SimpleNamespace(
            _toast=lambda msg: toasts.append(msg),
            reload=MagicMock(),
        )

        # 1. Modified artwork preserved
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch("glyph.window.discard_override", return_value={"icon_skipped": True, "icon_skip_reason": "modified"}):
            window.GlyphWindow._discard_overrides(win_stub, app)
            self.assertEqual(toasts[-1], "Discarded overrides for Discard App. Modified artwork was preserved.")

        # 2. General custom artwork preserved (e.g. unreadable, shared)
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch("glyph.window.discard_override", return_value={"icon_skipped": True, "icon_skip_reason": "unreadable"}):
            window.GlyphWindow._discard_overrides(win_stub, app)
            self.assertEqual(toasts[-1], "Discarded overrides for Discard App. Custom artwork was preserved.")

        # 3. Clean discard (icon deleted or no icon)
        with patch("glyph.window.can_delete_managed_icon", return_value=False), \
             patch("glyph.window.discard_override", return_value={"icon_deleted": True, "icon_skipped": False}):
            window.GlyphWindow._discard_overrides(win_stub, app)
            self.assertEqual(toasts[-1], "Discarded overrides for Discard App.")


    def test_show_icon_picker_no_themes_falls_back_to_file_dialog(self):
        cb = MagicMock()
        with patch("glyph.dialogs.list_installed_themes", return_value=[]):
            dialogs.show_icon_picker(None, SimpleNamespace(name="Test"), on_open_file_callback=cb)
        cb.assert_called_once()

    def test_show_icon_picker_with_themes_presents_dialog(self):
        themes = [SimpleNamespace(theme_id="test-theme", name="Test Theme", comment="", inherits=(), path="/path", hidden=False)]
        with patch("glyph.dialogs.list_installed_themes", return_value=themes), \
             patch("glyph.dialogs.search_theme_icons", return_value=[]), \
             patch("glyph.dialogs.Adw.Window") as mock_window, \
             patch("glyph.dialogs.Adw.ToolbarView"), \
             patch("glyph.dialogs.Adw.HeaderBar"), \
             patch("glyph.dialogs.Gtk") as mock_gtk:
            mock_gtk.DropDown.new.return_value.get_selected.return_value = 0
            mock_gtk.SearchEntry.return_value.get_text.return_value = ""
            mock_gtk.FlowBox.return_value.get_first_child.return_value = None
            dialogs.show_icon_picker(None, SimpleNamespace(name="Test", desktop_id="test.desktop"))
            mock_window.assert_called_once()
            mock_window.return_value.present.assert_called_once()


if __name__ == "__main__":
    unittest.main()


