import base64
import io
import json
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import glyph.overrides as ov
from glyph.overrides import (
    BatchResult,
    BackupPlan,
    LauncherModifiedExternallyError,
    OverrideError,
    apply_command,
    apply_icon,
    apply_name,
    apply_theme_icon,
    can_delete_managed_icon,
    check_managed_icon_deletion,
    discard_override,
    export_backup,
    import_backup,
    load_state,
    preview_backup,
    restore_all_to_stock,
    restore_stock_launcher,
    revert_all_icons,
    revert_all_names,
    revert_command,
    revert_icon,
    revert_name,
    save_state,
)


def _valid_png_bytes() -> bytes:
    # 1x1 transparent PNG
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


class TestOverrides(unittest.TestCase):
    def test_old_flatpak_state_and_journal_are_preserved(self):
        private = self.root / 'private'
        old_launcher = private / 'applications/demo.desktop'
        old_launcher.parent.mkdir(parents=True)
        old_launcher.write_text('[Desktop Entry]\nName=Old\n')
        state = json.dumps({'demo.desktop': {'local_desktop': str(old_launcher)}})
        self.state_file.write_text(state)
        with patch.object(ov, 'in_flatpak', return_value=True), patch.object(ov, 'XDG_DATA_HOME', private):
            with self.assertRaisesRegex(OverrideError, 'older Flatpak build'):
                load_state()
            self.assertEqual(self.state_file.read_text(), state)
            old_journal = self.data_dir / 'transaction.json'
            old_journal.write_text('[]')
            with self.assertRaisesRegex(OverrideError, 'older Flatpak build'):
                load_state()
            self.assertEqual(old_journal.read_text(), '[]')
            self.assertEqual(ov._journal_path().name, 'host-transaction.json')
        self.assertEqual(old_launcher.read_text(), '[Desktop Entry]\nName=Old\n')

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.app_dir = self.root / "applications"
        self.data_dir = self.root / "glyph"
        self.icons_dir = self.data_dir / "icons"
        self.state_file = self.data_dir / "overrides.json"

        self.stock_dir = self.root / "stock"
        self.stock_dir.mkdir(parents=True)

        self.patchers = [
            patch.object(ov, "APPLICATIONS_DIR", self.app_dir),
            patch.object(ov, "DATA_DIR", self.data_dir),
            patch.object(ov, "ICONS_DIR", self.icons_dir),
            patch.object(ov, "STATE_FILE", self.state_file),
            patch.object(ov, "refresh_desktop_database"),
        ]
        for p in self.patchers:
            p.start()
        ov._ensure_dirs()

        self.sample_png = self.root / "sample.png"
        self.sample_png.write_bytes(_valid_png_bytes())

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.tmp_dir.cleanup()

    def test_bulk_icon_revert_preserves_launcher_and_custom_name(self):
        # Finding 1 check: Pre-existing launcher with custom icon and custom name
        local_launcher = self.app_dir / "editor.desktop"
        local_launcher.write_text(
            "[Desktop Entry]\nName=Original Editor\nIcon=editor-icon\nType=Application\n",
            encoding="utf-8",
        )

        apply_icon("editor.desktop", str(local_launcher), str(self.sample_png))
        apply_name("editor.desktop", str(local_launcher), "Custom Editor")

        res = revert_all_icons()
        self.assertEqual(res.completed, 1)

        # Launcher must still exist because it was pre-existing and still has a custom name
        self.assertTrue(local_launcher.is_file())
        text = local_launcher.read_text(encoding="utf-8")
        self.assertIn("Icon=editor-icon", text)
        self.assertIn("Name=Custom Editor", text)

        state = load_state()
        self.assertIn("editor.desktop", state)
        self.assertNotIn("icon_path", state["editor.desktop"])
        self.assertEqual(state["editor.desktop"].get("custom_name"), "Custom Editor")

    def test_name_only_excluded_from_bulk_icon_revert(self):
        # Finding 1 check: Name-only app must not be touched by revert_all_icons
        launcher = self.app_dir / "nameonly.desktop"
        launcher.write_text(
            "[Desktop Entry]\nName=Initial Name\nIcon=init-icon\nType=Application\n",
            encoding="utf-8",
        )
        apply_name("nameonly.desktop", str(launcher), "New Display Name")

        res = revert_all_icons()
        self.assertEqual(res.completed, 0)

        text = launcher.read_text(encoding="utf-8")
        self.assertIn("Name=New Display Name", text)

    def test_stock_restore_preserves_local_only_launcher_and_icon(self):
        # Finding 2 check: Local-only launcher should NOT be deleted or stripped of icon
        local_only = self.app_dir / "custom-tool.desktop"
        local_only.write_text(
            "[Desktop Entry]\nName=Custom Tool\nIcon=tool-icon\nType=Application\n",
            encoding="utf-8",
        )
        apply_icon("custom-tool.desktop", str(local_only), str(self.sample_png))
        icon_path = Path(load_state()["custom-tool.desktop"]["icon_path"])
        self.assertTrue(icon_path.is_file())

        # Stock app that shadows a system launcher
        stock_file = self.stock_dir / "stock-app.desktop"
        stock_file.write_text(
            "[Desktop Entry]\nName=Stock App\nIcon=stock-icon\nType=Application\n",
            encoding="utf-8",
        )
        shadow_launcher = self.app_dir / "stock-app.desktop"
        with patch("glyph.overrides.find_stock", side_effect=lambda did: str(stock_file) if did == "stock-app.desktop" else ""):
            apply_icon("stock-app.desktop", str(stock_file), str(self.sample_png))
            shadow_icon = Path(load_state()["stock-app.desktop"]["icon_path"])

            res = restore_all_to_stock()
            self.assertEqual(res.completed, 1)

        # Stock-shadowed launcher removed
        self.assertFalse(shadow_launcher.exists())
        self.assertFalse(shadow_icon.exists())

        # Local-only launcher preserved
        self.assertTrue(local_only.is_file())
        self.assertTrue(icon_path.is_file())
        state = load_state()
        self.assertIn("custom-tool.desktop", state)
        self.assertNotIn("stock-app.desktop", state)

    def test_stock_restore_removes_matched_override_and_tracked_icon(self):
        # Finding 2 check: single app restore_stock_launcher
        stock_file = self.stock_dir / "gimp.desktop"
        stock_file.write_text(
            "[Desktop Entry]\nName=GNU Image Manipulation Program\nIcon=gimp\n",
            encoding="utf-8",
        )
        with patch("glyph.overrides.find_stock", return_value=str(stock_file)):
            apply_icon("gimp.desktop", str(stock_file), str(self.sample_png))
            local = self.app_dir / "gimp.desktop"
            self.assertTrue(local.is_file())
            icon = Path(load_state()["gimp.desktop"]["icon_path"])
            self.assertTrue(icon.is_file())

            restore_stock_launcher("gimp.desktop")
            self.assertFalse(local.exists())
            self.assertFalse(icon.exists())
            self.assertNotIn("gimp.desktop", load_state())

    def test_nested_system_desktop_id_produces_flattened_override(self):
        # Finding 6 check: nested desktop file in stock produces flattened ID override
        nested_stock = self.stock_dir / "kde" / "kwrite.desktop"
        nested_stock.parent.mkdir(parents=True)
        nested_stock.write_text("[Desktop Entry]\nName=KWrite\nIcon=kwrite\n", encoding="utf-8")

        desktop_id = "kde-kwrite.desktop"
        with patch("glyph.overrides.find_stock", return_value=str(nested_stock)):
            apply_icon(desktop_id, str(nested_stock), str(self.sample_png))

        expected_local = self.app_dir / "kde-kwrite.desktop"
        self.assertTrue(expected_local.is_file())
        self.assertFalse((self.app_dir / "kwrite.desktop").exists())

    def test_existing_nested_user_launcher_retains_location(self):
        # Finding 6 check: user nested launcher retains its subfolder path
        user_nested = self.app_dir / "mycategory" / "editor.desktop"
        user_nested.parent.mkdir(parents=True)
        user_nested.write_text(
            "[Desktop Entry]\nName=Nested Editor\nIcon=editor\n",
            encoding="utf-8",
        )
        desktop_id = "mycategory-editor.desktop"
        apply_name(desktop_id, str(user_nested), "Renamed Nested Editor")

        self.assertTrue(user_nested.is_file())
        self.assertIn("Name=Renamed Nested Editor", user_nested.read_text(encoding="utf-8"))
        self.assertEqual(load_state()[desktop_id]["local_desktop"], str(user_nested))

    def test_localized_name_preservation_and_revert(self):
        # Finding 7 check: Name[locale] entries are preserved on rename and restored on revert
        launcher = self.app_dir / "calc.desktop"
        launcher.write_text(
            "[Desktop Entry]\n"
            "Name=Calculator\n"
            "Name[fr]=Calculatrice\n"
            "Name[de]=Rechner\n"
            "Icon=calc\n",
            encoding="utf-8",
        )

        apply_name("calc.desktop", str(launcher), "Custom Calc")
        renamed_text = launcher.read_text(encoding="utf-8")
        self.assertIn("Name=Custom Calc", renamed_text)
        self.assertNotIn("Calculatrice", renamed_text)
        self.assertNotIn("Rechner", renamed_text)

        revert_name("calc.desktop")
        reverted_text = launcher.read_text(encoding="utf-8")
        self.assertIn("Name=Calculator", reverted_text)
        self.assertIn("Name[fr]=Calculatrice", reverted_text)
        self.assertIn("Name[de]=Rechner", reverted_text)

    def test_backup_v2_export_preview_import_roundtrip(self):
        # Finding 3 & 4 check: version 2 format with custom names and relative icons
        launcher = self.app_dir / "app1.desktop"
        launcher.write_text("[Desktop Entry]\nName=App 1\nIcon=app1\n", encoding="utf-8")
        apply_icon("app1.desktop", str(launcher), str(self.sample_png))
        apply_name("app1.desktop", str(launcher), "App 1 Custom")

        backup_file = self.root / "backup.tar.gz"
        count = export_backup(backup_file)
        self.assertEqual(count, 1)

        # Inspect backup archive contents
        with tarfile.open(backup_file, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("manifest.json", names)
            self.assertTrue(any(n.startswith("icons/") for n in names))
            manifest_file = tar.extractfile("manifest.json")
            if manifest_file is None:
                self.fail("Backup manifest.json must be a readable file")
            with manifest_file:
                manifest = json.loads(manifest_file.read().decode("utf-8"))
            self.assertEqual(manifest["version"], 2)
            self.assertIn("app1.desktop", manifest["entries"])
            self.assertEqual(manifest["entries"]["app1.desktop"]["name"], "App 1 Custom")

        # Preview
        with patch("glyph.overrides.find_stock", return_value=str(launcher)):
            plan = preview_backup(backup_file)
            self.assertFalse(plan.legacy)
            self.assertIn("app1.desktop", plan.entries)
            self.assertIn("app1.desktop", plan.conflicts)

            # Revert everything locally
            revert_icon("app1.desktop")
            revert_name("app1.desktop")
            self.assertEqual(load_state(), {})

            # By default (replace_existing=False), conflicting existing launcher is skipped
            res_skip = import_backup(backup_file, replace_existing=False)
            self.assertEqual(res_skip.completed, 0)
            self.assertIn("app1.desktop", res_skip.skipped)

            # With replace_existing=True, it replaces existing customizations
            res = import_backup(backup_file, replace_existing=True)
            self.assertEqual(res.completed, 1)

            state = load_state()
            self.assertIn("app1.desktop", state)
            self.assertEqual(state["app1.desktop"]["custom_name"], "App 1 Custom")
            self.assertTrue(Path(state["app1.desktop"]["icon_path"]).is_file())

    def test_backup_legacy_v1_compatibility(self):
        # Finding 3 & 4 check: backwards compatibility with legacy v1 backups
        launcher = self.app_dir / "legacy.desktop"
        launcher.write_text("[Desktop Entry]\nName=Legacy App\nIcon=legacy\n", encoding="utf-8")

        legacy_tar = self.root / "legacy.tar.gz"
        legacy_state = {
            "legacy.desktop": {
                "custom_name": "Old Custom Name",
                "icon_path": "/old/home/.local/share/glyph/icons/legacy-123.png",
                "created_local": True,
            }
        }
        png_data = _valid_png_bytes()
        with tarfile.open(legacy_tar, "w:gz") as tar:
            state_bytes = json.dumps(legacy_state).encode("utf-8")
            info = tarfile.TarInfo("overrides.json")
            info.size = len(state_bytes)
            tar.addfile(info, io.BytesIO(state_bytes))

            icon_info = tarfile.TarInfo("icons/legacy-123.png")
            icon_info.size = len(png_data)
            tar.addfile(icon_info, io.BytesIO(png_data))

        with patch("glyph.overrides.find_stock", return_value=str(launcher)):
            plan = preview_backup(legacy_tar)
            self.assertTrue(plan.legacy)
            self.assertIn("legacy.desktop", plan.entries)
            self.assertEqual(plan.entries["legacy.desktop"]["name"], "Old Custom Name")

            res = import_backup(legacy_tar, replace_existing=True)
            self.assertEqual(res.completed, 1)
            self.assertEqual(load_state()["legacy.desktop"]["custom_name"], "Old Custom Name")

    def test_transaction_journal_recovery(self):
        # Finding 11 check: interrupted transaction recovery
        test_file = self.app_dir / "target.desktop"
        test_file.write_text("[Desktop Entry]\nName=Before\n", encoding="utf-8")

        # Simulate journal written before crash
        journal = ov._journal_path()
        record = [{
            "kind": "launcher",
            "relative": "target.desktop",
            "before": base64.b64encode(b"[Desktop Entry]\nName=Before\n").decode(),
            "after": ov._digest(b"[Desktop Entry]\nName=After\n"),
            "mode": 0o644,
        }]
        journal.write_text(json.dumps(record), encoding="utf-8")

        # Simulate partial file write
        test_file.write_text("[Desktop Entry]\nName=After\n", encoding="utf-8")

        # Calling any operation should trigger _restore_journal
        state = load_state()
        self.assertEqual(state, {})
        self.assertFalse(journal.exists())
        # The journal restores the 'before' content
        self.assertEqual(test_file.read_text(encoding="utf-8"), "[Desktop Entry]\nName=Before\n")

    def test_corrupted_state_file_fails_closed_and_preserves_file(self):
        # Finding 11 check: bad json in state file must fail closed and preserve file
        self.state_file.write_text("{ corrupt json ...", encoding="utf-8")
        with self.assertRaises(OverrideError):
            load_state()
        self.assertTrue(self.state_file.is_file())
        self.assertIn("corrupt", self.state_file.read_text(encoding="utf-8"))

    def test_bulk_names_preserves_icon_and_other_fields(self):
        p = self.app_dir / "demo.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=Original\nExec=/bin/true\nIcon=orig-icon\n", encoding="utf-8")
        apply_icon("demo.desktop", str(p), str(self.sample_png))
        apply_name("demo.desktop", str(p), "Custom Name")

        res = revert_all_names()
        self.assertEqual(res.completed, 1)
        self.assertEqual(res.errors, {})

        content = p.read_text(encoding="utf-8")
        self.assertIn("Name=Original", content)
        self.assertIn("Icon=", content)
        self.assertNotIn("Custom Name", content)

        state = load_state()
        self.assertIn("demo.desktop", state)
        self.assertIn("icon_path", state["demo.desktop"])
        self.assertNotIn("custom_name", state["demo.desktop"])

    def test_bulk_reverts_order_independence(self):
        # Test 1: Revert names first, then icons
        p1 = self.app_dir / "app1.desktop"
        p1.write_text("[Desktop Entry]\nName=App1\nIcon=icon1\n", encoding="utf-8")
        apply_icon("app1.desktop", str(p1), str(self.sample_png))
        apply_name("app1.desktop", str(p1), "Named1")

        res_n = revert_all_names()
        self.assertEqual(res_n.completed, 1)
        self.assertIn("Name=App1", p1.read_text(encoding="utf-8"))

        res_i = revert_all_icons()
        self.assertEqual(res_i.completed, 1)
        self.assertIn("Icon=icon1", p1.read_text(encoding="utf-8"))

        # Test 2: Revert icons first, then names
        p2 = self.app_dir / "app2.desktop"
        p2.write_text("[Desktop Entry]\nName=App2\nIcon=icon2\n", encoding="utf-8")
        apply_icon("app2.desktop", str(p2), str(self.sample_png))
        apply_name("app2.desktop", str(p2), "Named2")

        res_i2 = revert_all_icons()
        self.assertEqual(res_i2.completed, 1)
        self.assertIn("Icon=icon2", p2.read_text(encoding="utf-8"))
        self.assertIn("Name=Named2", p2.read_text(encoding="utf-8"))

        res_n2 = revert_all_names()
        self.assertEqual(res_n2.completed, 1)
        self.assertIn("Name=App2", p2.read_text(encoding="utf-8"))

    def test_legacy_created_override_undo_removes_copy(self):
        stock = self.stock_dir / "stock.desktop"
        stock.write_text("[Desktop Entry]\nName=Stock\nExec=/bin/true\n", encoding="utf-8")
        p = self.app_dir / "stock.desktop"
        p.write_text("[Desktop Entry]\nName=Custom\nExec=/bin/true\n", encoding="utf-8")

        legacy = {
            "stock.desktop": {
                "created_local": True,
                "local_desktop": str(p),
                "source_path": str(stock),
                "original_name": "Stock",
                "custom_name": "Custom",
            }
        }
        self.state_file.write_text(json.dumps(legacy), encoding="utf-8")
        revert_name("stock.desktop")
        self.assertFalse(p.exists(), "v0.1.2 created override remains after its state is discarded")

    def test_legacy_created_override_undo_preserves_when_externally_edited(self):
        stock = self.stock_dir / "stock2.desktop"
        stock.write_text("[Desktop Entry]\nName=Stock2\nExec=/bin/true\n", encoding="utf-8")
        p = self.app_dir / "stock2.desktop"
        # User manually added Comment=External
        p.write_text("[Desktop Entry]\nName=Custom\nExec=/bin/true\nComment=External\n", encoding="utf-8")

        legacy = {
            "stock2.desktop": {
                "created_local": True,
                "local_desktop": str(p),
                "source_path": str(stock),
                "original_name": "Stock2",
                "custom_name": "Custom",
            }
        }
        self.state_file.write_text(json.dumps(legacy), encoding="utf-8")
        revert_name("stock2.desktop")
        # Should not delete file because it has external comments not in stock
        self.assertTrue(p.exists())
        self.assertIn("Comment=External", p.read_text(encoding="utf-8"))
        self.assertIn("Name=Stock2", p.read_text(encoding="utf-8"))

    def test_write_failure_rolls_back_files(self):
        p = self.app_dir / "rollback.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=Demo\nExec=/bin/true\nIcon=demo\n", encoding="utf-8")
        before = p.read_bytes()
        original_atomic = ov._atomic
        failed = False

        def write(path, data, mode=0o644):
            nonlocal failed
            if path == ov.STATE_FILE and not failed:
                failed = True
                raise OSError("simulated state write failure")
            return original_atomic(path, data, mode)

        with patch.object(ov, "_atomic", side_effect=write):
            with self.assertRaises(OverrideError):
                apply_name(p.name, str(p), "Changed")
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(load_state(), {})
        self.assertFalse(ov._journal_path().exists())

    def test_revert_detects_external_edit(self):
        p = self.app_dir / "ext.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=Demo\nExec=/bin/true\nIcon=demo\n", encoding="utf-8")
        apply_name(p.name, str(p), "Custom")
        p.write_text(p.read_text(encoding="utf-8") + "Comment=External\n", encoding="utf-8")
        before = p.read_bytes()
        with self.assertRaises(OverrideError):
            revert_name(p.name)
        self.assertEqual(p.read_bytes(), before)

    def test_directory_escape_backup_rejected(self):
        target = self.root / "bad.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            member = tarfile.TarInfo("../outside")
            member.size = 0
            archive.addfile(member, io.BytesIO())
        with self.assertRaises(OverrideError):
            preview_backup(target)

    def test_missing_import_is_reported_without_state_loss(self):
        p = self.app_dir / "keep.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=Keep\nExec=/bin/true\nIcon=keep\n", encoding="utf-8")
        apply_name(p.name, str(p), "Keep")

        target = self.root / "missing.tar.gz"
        payload = json.dumps({"version": 2, "entries": {"absent.desktop": {"name": "Absent"}}}).encode()
        with tarfile.open(target, "w:gz") as archive:
            member = tarfile.TarInfo("manifest.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

        with patch.object(ov, "find_stock", return_value=""):
            result = import_backup(target)
        self.assertEqual(result.skipped, ["absent.desktop"])
        self.assertEqual(load_state()[p.name]["custom_name"], "Keep")

    def test_240_byte_desktop_id_customization_and_undo(self):
        # Construct desktop_id with exactly 240 bytes: 232 'a's + '.desktop' (8 bytes)
        desktop_id = "a" * 232 + ".desktop"
        self.assertEqual(len(desktop_id.encode("utf-8")), 240)

        p = self.app_dir / desktop_id
        p.write_text("[Desktop Entry]\nType=Application\nName=LongID\nExec=/bin/true\nIcon=longid\n", encoding="utf-8")

        # Apply custom icon and custom name
        apply_icon(desktop_id, str(p), str(self.sample_png))
        apply_name(desktop_id, str(p), "Custom Long ID Name")

        state = load_state()
        self.assertIn(desktop_id, state)
        icon_path = Path(state[desktop_id]["icon_path"])
        self.assertTrue(icon_path.is_file())
        self.assertLessEqual(len(icon_path.name.encode("utf-8")), 255)
        self.assertEqual(state[desktop_id]["custom_name"], "Custom Long ID Name")

        # Revert icon and name (undo)
        revert_icon(desktop_id)
        state_after_icon_revert = load_state()
        self.assertNotIn("icon_path", state_after_icon_revert[desktop_id])
        self.assertFalse(icon_path.exists())

        revert_name(desktop_id)
        state_after_name_revert = load_state()
        self.assertNotIn(desktop_id, state_after_name_revert)

        # Subsequent operations remain fully usable
        apply_name(desktop_id, str(p), "Second Custom Name")
        self.assertEqual(load_state()[desktop_id]["custom_name"], "Second Custom Name")

    def test_failure_recovery_does_not_leave_blocking_journal(self):
        p = self.app_dir / "recovery_test.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=RecoveryTest\nExec=/bin/true\nIcon=test\n", encoding="utf-8")
        original_atomic = ov._atomic
        failed = False

        def failing_atomic(path, data, mode=0o644):
            nonlocal failed
            # Allow writing transaction.json, but fail once on the first file write in commit
            if path != ov._journal_path() and not failed:
                failed = True
                raise OSError("simulated I/O disk full error during commit write")
            return original_atomic(path, data, mode)

        with patch.object(ov, "_atomic", side_effect=failing_atomic):
            with self.assertRaises(OverrideError):
                apply_name(p.name, str(p), "Fail Name")

        # Journal must not be left behind
        self.assertFalse(ov._journal_path().exists(), "Journal must be unlinked after rollback")

        # Subsequent operations must succeed and not be blocked
        apply_name(p.name, str(p), "Succeed Name")
        self.assertEqual(load_state()[p.name]["custom_name"], "Succeed Name")

    def test_external_modification_raises_launcher_modified_externally_error(self):
        p = self.app_dir / "external_test.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=External\nIcon=stock-icon\n", encoding="utf-8")
        apply_icon("external_test.desktop", str(p), str(self.sample_png))
        self.assertIn("external_test.desktop", load_state())

        # Simulate external edit (e.g. app update or integrator)
        p.write_text("[Desktop Entry]\nType=Application\nName=External Updated\nIcon=stock-icon\n", encoding="utf-8")

        with self.assertRaises(LauncherModifiedExternallyError) as ctx:
            apply_icon("external_test.desktop", str(p), str(self.sample_png))
        self.assertEqual(ctx.exception.desktop_id, "external_test.desktop")

        with self.assertRaises(LauncherModifiedExternallyError) as ctx:
            revert_icon("external_test.desktop")
        self.assertEqual(ctx.exception.desktop_id, "external_test.desktop")

        with self.assertRaises(LauncherModifiedExternallyError) as ctx:
            apply_name("external_test.desktop", str(p), "New Name")
        self.assertEqual(ctx.exception.desktop_id, "external_test.desktop")

    def test_external_modification_force_apply_succeeds_and_updates_hash(self):
        p = self.app_dir / "external_force.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=ForceApp\nIcon=stock-icon\n", encoding="utf-8")
        apply_icon("external_force.desktop", str(p), str(self.sample_png))

        # External modification
        p.write_text("[Desktop Entry]\nType=Application\nName=ForceApp Updated\nIcon=stock-icon\n", encoding="utf-8")

        # Force apply icon
        apply_icon("external_force.desktop", str(p), str(self.sample_png), force=True)
        state = load_state()
        self.assertIn("external_force.desktop", state)
        current_hash = ov._digest(p.read_bytes())
        self.assertEqual(state["external_force.desktop"]["last_hash"], current_hash)

    def test_discard_override_removes_state_and_cleans_icon_asset(self):
        p = self.app_dir / "discard_test.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=DiscardApp\nIcon=stock-icon\n", encoding="utf-8")
        apply_icon("discard_test.desktop", str(p), str(self.sample_png))

        state = load_state()
        icon_path = Path(state["discard_test.desktop"]["icon_path"])
        self.assertTrue(icon_path.exists())

        # Modify launcher externally
        p.write_text("[Desktop Entry]\nType=Application\nName=DiscardApp External\nIcon=stock-icon\n", encoding="utf-8")

        # Discard override with delete_icon=True (default)
        discard_override("discard_test.desktop", delete_icon=True)
        self.assertNotIn("discard_test.desktop", load_state())
        self.assertFalse(icon_path.exists(), "Unreferenced custom icon should be unlinked")
        self.assertEqual(p.read_text(encoding="utf-8"), "[Desktop Entry]\nType=Application\nName=DiscardApp External\nIcon=stock-icon\n")

    def test_conflict_force_rebase_preserves_other_overrides_and_external_keys(self):
        # 1. Start with custom name, icon, and command values
        p = self.app_dir / "conflict_app.desktop"
        p.write_text(
            "[Desktop Entry]\nType=Application\nName=Stock Name\nExec=/usr/bin/stock\nIcon=stock-icon\nCategories=Utility;\n",
            encoding="utf-8",
        )
        apply_name("conflict_app.desktop", str(p), "Custom Name")
        apply_command("conflict_app.desktop", str(p), "custom-exec --flag")
        apply_icon("conflict_app.desktop", str(p), str(self.sample_png))

        state = load_state()["conflict_app.desktop"]
        saved_icon = state["icon_path"]
        self.assertEqual(state["custom_name"], "Custom Name")
        self.assertEqual(state["custom_command"], "custom-exec --flag")

        # 2. External edit changes Name, Exec, Icon lines, and adds unrelated lines/keys
        p.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=External Update\n"
            "Exec=/usr/bin/external-updated\n"
            "Icon=external-icon\n"
            "Comment=External integrator comment\n"
            "X-App-Release=1.2.3\n"
            "Categories=Utility;System;\n",
            encoding="utf-8",
        )

        # 3. Without force, modifying or reverting raises LauncherModifiedExternallyError
        with self.assertRaises(LauncherModifiedExternallyError):
            apply_icon("conflict_app.desktop", str(p), str(self.sample_png), force=False)

        # 4. Force apply icon (e.g. user accepted Overwrite on icon change)
        new_png = self.root / "new_icon.png"
        new_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x55" * 64)
        apply_icon("conflict_app.desktop", str(p), str(new_png), force=True)

        content = p.read_text(encoding="utf-8")
        # Requested new icon is present
        new_state = load_state()["conflict_app.desktop"]
        self.assertIn(f"Icon={new_state['icon_path']}", content)
        # Saved custom name and custom command are preserved
        self.assertIn("Name=Custom Name", content)
        self.assertIn("Exec=custom-exec --flag", content)
        # Unrelated external fields survive the rebase
        self.assertIn("Comment=External integrator comment", content)
        self.assertIn("X-App-Release=1.2.3", content)
        self.assertIn("Categories=Utility;System;", content)
        # last_hash matches the final file bytes on disk
        self.assertEqual(new_state["last_hash"], ov._digest(p.read_bytes()))

        # 5. External edit again
        p.write_text(
            p.read_text(encoding="utf-8") + "X-Another-Key=PreservedValue\n",
            encoding="utf-8",
        )

        # Force apply name: retains custom icon and custom command
        apply_name("conflict_app.desktop", str(p), "Brand New Name", force=True)
        content2 = p.read_text(encoding="utf-8")
        self.assertIn("Name=Brand New Name", content2)
        self.assertIn(f"Icon={new_state['icon_path']}", content2)
        self.assertIn("Exec=custom-exec --flag", content2)
        self.assertIn("X-Another-Key=PreservedValue", content2)

        # Force apply command: retains custom icon and custom name
        apply_command("conflict_app.desktop", str(p), "brand-new-cmd", force=True)
        content3 = p.read_text(encoding="utf-8")
        self.assertIn("Exec=brand-new-cmd", content3)
        self.assertIn("Name=Brand New Name", content3)
        self.assertIn(f"Icon={new_state['icon_path']}", content3)

    def test_discard_with_keep_and_delete_image_preserves_usable_launcher(self):
        p = self.app_dir / "usable_app.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=Stock App\nIcon=stock-icon\n", encoding="utf-8")
        apply_icon("usable_app.desktop", str(p), str(self.sample_png))

        state = load_state()
        icon_path = Path(state["usable_app.desktop"]["icon_path"])
        self.assertTrue(icon_path.is_file())

        # Externally edit only the launcher's name
        p.write_text(f"[Desktop Entry]\nType=Application\nName=Stock App Edited\nIcon={icon_path}\n", encoding="utf-8")

        # Discard with keep (delete_icon=False)
        discard_override("usable_app.desktop", delete_icon=False)
        self.assertNotIn("usable_app.desktop", load_state())
        # Kept image remains in ICONS_DIR
        self.assertTrue(icon_path.is_file())
        # Launcher is untouched on discard when image is kept
        text_kept = p.read_text(encoding="utf-8")
        self.assertIn("Name=Stock App Edited", text_kept)
        self.assertIn(f"Icon={icon_path}", text_kept)

        # Re-setup stock launcher, apply icon and edit externally again to test delete_icon=True
        p.write_text("[Desktop Entry]\nType=Application\nName=Stock App\nIcon=stock-icon\n", encoding="utf-8")
        apply_icon("usable_app.desktop", str(p), str(self.sample_png))
        icon_path2 = Path(load_state()["usable_app.desktop"]["icon_path"])
        p.write_text(f"[Desktop Entry]\nType=Application\nName=Stock App Edited 2\nIcon={icon_path2}\n", encoding="utf-8")

        # Discard with delete (delete_icon=True)
        discard_override("usable_app.desktop", delete_icon=True)
        self.assertNotIn("usable_app.desktop", load_state())
        # Image is deleted
        self.assertFalse(icon_path2.exists())
        # Launcher Icon key was updated to original/stock icon; does not point to deleted file
        text_deleted = p.read_text(encoding="utf-8")
        self.assertIn("Name=Stock App Edited 2", text_deleted)
        self.assertNotIn(str(icon_path2), text_deleted)
        self.assertIn("Icon=stock-icon", text_deleted)

    def test_keep_vs_delete_artwork_on_replace_theme_switch_and_revert(self):
        p = self.app_dir / "artwork_app.desktop"
        p.write_text("[Desktop Entry]\nType=Application\nName=Art App\nIcon=stock-art\n", encoding="utf-8")
        apply_icon("artwork_app.desktop", str(p), str(self.sample_png))
        icon_v1 = Path(load_state()["artwork_app.desktop"]["icon_path"])
        self.assertTrue(icon_v1.is_file())

        # 1. Replace with delete_icon=False: keeps icon_v1
        icon2_file = self.root / "art2.png"
        icon2_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x66" * 64)
        apply_icon("artwork_app.desktop", str(p), str(icon2_file), delete_icon=False)
        icon_v2 = Path(load_state()["artwork_app.desktop"]["icon_path"])
        self.assertTrue(icon_v1.exists(), "Kept artwork must remain available in icon storage")
        self.assertTrue(icon_v2.exists())

        # 2. Replace with delete_icon=True: deletes icon_v2
        icon3_file = self.root / "art3.png"
        icon3_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x77" * 64)
        apply_icon("artwork_app.desktop", str(p), str(icon3_file), delete_icon=True)
        icon_v3 = Path(load_state()["artwork_app.desktop"]["icon_path"])
        self.assertFalse(icon_v2.exists(), "Deleted artwork must be removed")
        self.assertTrue(icon_v3.exists())

        # 3. Switch to theme icon with delete_icon=False: keeps icon_v3
        apply_theme_icon("artwork_app.desktop", str(p), "system-file-manager", delete_icon=False)
        self.assertTrue(icon_v3.exists(), "Theme switch with keep must preserve previous artwork")
        self.assertNotIn("icon_path", load_state()["artwork_app.desktop"])
        self.assertEqual(load_state()["artwork_app.desktop"]["theme_icon"], "system-file-manager")

        # 4. Apply file icon then switch to theme icon with delete_icon=True: deletes icon
        apply_icon("artwork_app.desktop", str(p), str(icon3_file))
        icon_v4 = Path(load_state()["artwork_app.desktop"]["icon_path"])
        self.assertTrue(icon_v4.exists())
        apply_theme_icon("artwork_app.desktop", str(p), "utilities-terminal", delete_icon=True)
        self.assertFalse(icon_v4.exists(), "Theme switch with delete must remove prior artwork")

        # 5. Revert icon with keep vs delete
        apply_icon("artwork_app.desktop", str(p), str(self.sample_png))
        icon_v5 = Path(load_state()["artwork_app.desktop"]["icon_path"])
        self.assertTrue(icon_v5.exists())
        revert_icon("artwork_app.desktop", delete_icon=False)
        self.assertTrue(icon_v5.exists(), "Revert icon with keep preserves artwork file")

        apply_icon("artwork_app.desktop", str(p), str(self.sample_png))
        icon_v6 = Path(load_state()["artwork_app.desktop"]["icon_path"])
        revert_icon("artwork_app.desktop", delete_icon=True)
        self.assertFalse(icon_v6.exists(), "Revert icon with delete removes artwork file")

    def test_shared_and_externally_modified_icons_are_never_deleted(self):
        p1 = self.app_dir / "app_share1.desktop"
        p2 = self.app_dir / "app_share2.desktop"
        p1.write_text("[Desktop Entry]\nName=Share1\nIcon=stock1\n", encoding="utf-8")
        p2.write_text("[Desktop Entry]\nName=Share2\nIcon=stock2\n", encoding="utf-8")

        apply_icon("app_share1.desktop", str(p1), str(self.sample_png))
        state = load_state()
        shared_icon = Path(state["app_share1.desktop"]["icon_path"])

        # Share the identical icon path in state with app 2
        p2.write_text(f"[Desktop Entry]\nName=Share2\nIcon={shared_icon}\n", encoding="utf-8")
        state["app_share2.desktop"] = {
            "local_desktop": str(p2),
            "icon_path": str(shared_icon),
            "icon_hash": state["app_share1.desktop"]["icon_hash"],
            "original_icon": "stock2",
            "last_hash": ov._digest(p2.read_bytes()),
        }
        ov.save_state(state)

        # Both apps reference the identical image file in ICONS_DIR
        state = load_state()
        self.assertEqual(state["app_share1.desktop"]["icon_path"], state["app_share2.desktop"]["icon_path"])
        self.assertFalse(can_delete_managed_icon("app_share1.desktop"))
        eligible, _, reason = check_managed_icon_deletion("app_share1.desktop")
        self.assertFalse(eligible)
        self.assertEqual(reason, "shared")

        # Discard app 1 with delete_icon=True: must NOT delete shared icon
        discard_override("app_share1.desktop", delete_icon=True)
        self.assertTrue(shared_icon.exists(), "Shared icon must not be deleted while still referenced by app 2")

        # Externally modify the icon file so its hash no longer matches
        shared_icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff" * 64)
        self.assertFalse(can_delete_managed_icon("app_share2.desktop"))
        eligible, _, reason = check_managed_icon_deletion("app_share2.desktop")
        self.assertFalse(eligible)
        self.assertEqual(reason, "modified")

        # Reverting app 2 with delete_icon=True must preserve the modified icon
        revert_icon("app_share2.desktop", delete_icon=True)
        self.assertTrue(shared_icon.exists(), "Externally modified icon must be preserved")

    def test_backup_command_coverage_and_omitted_counts(self):
        p_cmd = self.app_dir / "cmd_only.desktop"
        p_cmd.write_text("[Desktop Entry]\nName=CmdApp\nExec=/bin/true\nIcon=cmd\n", encoding="utf-8")
        apply_command("cmd_only.desktop", str(p_cmd), "custom-cmd-flag")

        tar_cmd_only = self.root / "cmd_only_backup.tar.gz"
        res1 = export_backup(tar_cmd_only)
        self.assertEqual(res1.exported, 0)
        self.assertEqual(res1.omitted, 1)
        self.assertEqual(res1.command_only, 1)
        self.assertEqual(res1.mixed, 0)
        self.assertEqual(int(res1), 0)

        # Mix of name, icon, and command customizations
        p_mix = self.app_dir / "mix.desktop"
        p_mix.write_text("[Desktop Entry]\nName=MixApp\nExec=/bin/true\nIcon=mix\n", encoding="utf-8")
        apply_name("mix.desktop", str(p_mix), "Mix Custom")
        apply_icon("mix.desktop", str(p_mix), str(self.sample_png))
        apply_command("mix.desktop", str(p_mix), "mix-exec")

        p_name = self.app_dir / "name_only.desktop"
        p_name.write_text("[Desktop Entry]\nName=NameOnly\nExec=/bin/true\nIcon=name\n", encoding="utf-8")
        apply_name("name_only.desktop", str(p_name), "Name Only Custom")

        tar_mix = self.root / "mix_backup.tar.gz"
        res2 = export_backup(tar_mix)
        self.assertEqual(res2.exported, 2)  # mix.desktop and name_only.desktop
        self.assertEqual(res2.omitted, 2)   # mix.desktop (mixed) and cmd_only.desktop (command-only)
        self.assertEqual(res2.command_only, 1)
        self.assertEqual(res2.mixed, 1)
        self.assertEqual(int(res2), 2)

        # Verify archive manifest excludes commands
        with tarfile.open(tar_mix, "r:gz") as tar:
            manifest_file = tar.extractfile("manifest.json")
            self.assertIsNotNone(manifest_file)
            manifest = json.loads(manifest_file.read().decode("utf-8"))
            self.assertEqual(manifest["version"], 2)
            self.assertIn("mix.desktop", manifest["entries"])
            self.assertIn("name_only.desktop", manifest["entries"])
            self.assertNotIn("cmd_only.desktop", manifest["entries"])
            # Entries contain only name and icon
            for entry in manifest["entries"].values():
                self.assertTrue(set(entry.keys()).issubset({"name", "icon"}))

    def test_force_apply_rebases_saved_originals_for_reset(self):
        p = self.app_dir / "rebase_app.desktop"
        p.write_text("[Desktop Entry]\nName=Baseline App\nExec=baseline-exec\nIcon=baseline-icon\nType=Application\n", encoding="utf-8")

        # Set up custom name, icon, and command
        apply_name("rebase_app.desktop", str(p), "My Custom Name")
        apply_command("rebase_app.desktop", str(p), "my-custom-exec")
        apply_icon("rebase_app.desktop", str(p), str(self.sample_png))
        icon_v1 = Path(load_state()["rebase_app.desktop"]["icon_path"])

        # Externally update all three fields and add an unrelated key
        p.write_text(
            f"[Desktop Entry]\nName=Upstream Name\nExec=upstream-exec\nIcon=upstream-icon\nType=Application\nX-Vendor-Custom=PreserveMe\n",
            encoding="utf-8"
        )

        # Force-apply a new icon
        icon2_file = self.root / "new_art.png"
        icon2_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x99" * 64)
        apply_icon("rebase_app.desktop", str(p), str(icon2_file), force=True)

        state = load_state()["rebase_app.desktop"]
        icon_v2 = Path(state["icon_path"])
        text = p.read_text(encoding="utf-8")

        # Other custom values remain active
        self.assertIn("Name=My Custom Name", text)
        self.assertIn("Exec=my-custom-exec", text)
        self.assertIn(f"Icon={icon_v2}", text)
        # Unrelated key survives
        self.assertIn("X-Vendor-Custom=PreserveMe", text)
        # Hash matches final written bytes
        self.assertEqual(state["last_hash"], ov._digest(text.encode("utf-8")))

        # Resetting each field restores its externally updated baseline
        revert_name("rebase_app.desktop")
        text_after_name = p.read_text(encoding="utf-8")
        self.assertIn("Name=Upstream Name", text_after_name)
        self.assertIn("Exec=my-custom-exec", text_after_name)

        revert_command("rebase_app.desktop")
        text_after_cmd = p.read_text(encoding="utf-8")
        self.assertIn("Exec=upstream-exec", text_after_cmd)

        revert_icon("rebase_app.desktop")
        text_after_icon = p.read_text(encoding="utf-8")
        self.assertIn("Icon=upstream-icon", text_after_icon)
        self.assertIn("X-Vendor-Custom=PreserveMe", text_after_icon)

    def test_force_revert_uses_current_external_baseline(self):
        p = self.app_dir / "revert_rebase.desktop"
        p.write_text("[Desktop Entry]\nName=Old Name\nExec=old-exec\nType=Application\n", encoding="utf-8")

        apply_name("revert_rebase.desktop", str(p), "Custom Name")
        apply_command("revert_rebase.desktop", str(p), "custom-exec")

        # External edit changes name, command, and adds a key
        p.write_text("[Desktop Entry]\nName=External Name 2\nExec=external-exec-2\nType=Application\nX-Keep=Yes\n", encoding="utf-8")

        # Force revert name
        revert_name("revert_rebase.desktop", force=True)
        text = p.read_text(encoding="utf-8")

        # Name restores to external baseline; command remains custom
        self.assertIn("Name=External Name 2", text)
        self.assertIn("Exec=custom-exec", text)
        self.assertIn("X-Keep=Yes", text)
        state = load_state()["revert_rebase.desktop"]
        self.assertEqual(state["last_hash"], ov._digest(text.encode("utf-8")))

        # Reverting command restores to external baseline
        revert_command("revert_rebase.desktop")
        text_final = p.read_text(encoding="utf-8")
        self.assertIn("Exec=external-exec-2", text_final)

    def test_discard_override_launcher_reference_and_transaction_handling(self):
        # 1. Local launcher with NO original Icon key
        p_no_icon = self.app_dir / "no_icon.desktop"
        p_no_icon.write_text("[Desktop Entry]\nType=Application\nName=NoIconApp\nX-Other=True\n", encoding="utf-8")
        apply_icon("no_icon.desktop", str(p_no_icon), str(self.sample_png))
        icon1 = Path(load_state()["no_icon.desktop"]["icon_path"])
        self.assertTrue(icon1.is_file())

        # External edit leaves Icon pointed at managed icon, changes another key
        p_no_icon.write_text(f"[Desktop Entry]\nType=Application\nName=NoIconApp Edited\nIcon={icon1}\nX-Other=Changed\n", encoding="utf-8")

        res = discard_override("no_icon.desktop", delete_icon=True)
        self.assertFalse(res["icon_skipped"])
        self.assertFalse(icon1.exists(), "Managed icon must be deleted")
        text_no_icon = p_no_icon.read_text(encoding="utf-8")
        self.assertNotIn(str(icon1), text_no_icon)
        self.assertNotIn("Icon=", text_no_icon, "Icon key should be removed when no fallback exists")
        self.assertIn("Name=NoIconApp Edited", text_no_icon)
        self.assertIn("X-Other=Changed", text_no_icon)

        # 2. Launcher with stock fallback
        p_stock = self.app_dir / "has_stock.desktop"
        p_stock.write_text("[Desktop Entry]\nType=Application\nName=HasStock\nIcon=stock-ico\n", encoding="utf-8")
        stock_file = self.stock_dir / "has_stock.desktop"
        stock_file.write_text("[Desktop Entry]\nType=Application\nName=HasStock\nIcon=stock-ico\n", encoding="utf-8")

        with patch("glyph.overrides.find_stock", return_value=str(stock_file)):
            apply_icon("has_stock.desktop", str(p_stock), str(self.sample_png))
            icon2 = Path(load_state()["has_stock.desktop"]["icon_path"])
            p_stock.write_text(f"[Desktop Entry]\nType=Application\nName=HasStock Edited\nIcon={icon2}\n", encoding="utf-8")

            res_stock = discard_override("has_stock.desktop", delete_icon=True)
            self.assertFalse(res_stock["icon_skipped"])
            self.assertFalse(icon2.exists())
            text_stock = p_stock.read_text(encoding="utf-8")
            self.assertIn("Icon=stock-ico", text_stock)

        # 3. Simulate unreadable launcher on discard -> preserves image and reports skipped
        p_unread = self.app_dir / "unread.desktop"
        p_unread.write_text("[Desktop Entry]\nType=Application\nName=Unread\nIcon=original-icon\n", encoding="utf-8")
        apply_icon("unread.desktop", str(p_unread), str(self.sample_png))
        icon3 = Path(load_state()["unread.desktop"]["icon_path"])
        p_unread.write_text(f"[Desktop Entry]\nType=Application\nName=Unread Edited\nIcon={icon3}\n", encoding="utf-8")

        orig_read = ov._read
        def read_fail_on_launcher(path, limit):
            if path == p_unread:
                raise PermissionError("Permission denied")
            return orig_read(path, limit)

        with patch.object(ov, "_read", side_effect=read_fail_on_launcher):
            res_unread = discard_override("unread.desktop", delete_icon=True)
            self.assertTrue(res_unread["icon_skipped"])
            self.assertTrue(icon3.exists(), "Image must remain on disk if launcher rewrite fails")
            self.assertNotIn("unread.desktop", load_state())

        # 4. Keep choice removes tracking state without altering launcher or deleting image
        p_keep = self.app_dir / "keep.desktop"
        p_keep.write_text("[Desktop Entry]\nType=Application\nName=KeepApp\nIcon=orig-icon\n", encoding="utf-8")
        apply_icon("keep.desktop", str(p_keep), str(self.sample_png))
        icon4 = Path(load_state()["keep.desktop"]["icon_path"])
        p_keep.write_text(f"[Desktop Entry]\nType=Application\nName=KeepApp Edited\nIcon={icon4}\n", encoding="utf-8")

        res_keep = discard_override("keep.desktop", delete_icon=False)
        self.assertFalse(res_keep["icon_skipped"])
        self.assertTrue(icon4.exists())
        self.assertIn(f"Icon={icon4}", p_keep.read_text(encoding="utf-8"))
        self.assertNotIn("keep.desktop", load_state())

    def test_bulk_icon_revert_and_stock_restore_with_delete_icon_option(self):
        p1 = self.app_dir / "bulk1.desktop"
        p1.write_text("[Desktop Entry]\nName=Bulk1\nIcon=b1\n", encoding="utf-8")
        apply_icon("bulk1.desktop", str(p1), str(self.sample_png))
        icon_bulk1 = Path(load_state()["bulk1.desktop"]["icon_path"])

        # Revert all icons with delete_icon=False preserves file
        res_keep = revert_all_icons(delete_icon=False)
        self.assertEqual(res_keep.completed, 1)
        self.assertTrue(icon_bulk1.exists())

        # Re-apply and revert with delete_icon=True deletes file
        apply_icon("bulk1.desktop", str(p1), str(self.sample_png))
        icon_bulk2 = Path(load_state()["bulk1.desktop"]["icon_path"])
        res_del = revert_all_icons(delete_icon=True)
        self.assertEqual(res_del.completed, 1)
        self.assertFalse(icon_bulk2.exists())

        # Stock restore with delete_icon=False vs True
        stock_file = self.stock_dir / "bulk_stock.desktop"
        stock_file.write_text("[Desktop Entry]\nName=StockBulk\nIcon=stock-ico\n", encoding="utf-8")

        with patch("glyph.overrides.find_stock", return_value=str(stock_file)):
            apply_icon("bulk_stock.desktop", str(stock_file), str(self.sample_png))
            icon_s1 = Path(load_state()["bulk_stock.desktop"]["icon_path"])
            restore_all_to_stock(delete_icon=False)
            self.assertTrue(icon_s1.exists())

            # Re-apply and restore with delete_icon=True
            apply_icon("bulk_stock.desktop", str(stock_file), str(self.sample_png))
            icon_s2 = Path(load_state()["bulk_stock.desktop"]["icon_path"])
            restore_all_to_stock(delete_icon=True)
            self.assertFalse(icon_s2.exists())


if __name__ == "__main__":
    unittest.main()
