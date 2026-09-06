import base64
import io
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import glyph.overrides as ov
from glyph.overrides import (
    BatchResult,
    BackupPlan,
    OverrideError,
    apply_icon,
    apply_name,
    export_backup,
    import_backup,
    load_state,
    preview_backup,
    restore_all_to_stock,
    restore_stock_launcher,
    revert_all_icons,
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
        shadow_launcher.write_text(
            "[Desktop Entry]\nName=Stock App\nIcon=stock-icon\nType=Application\n",
            encoding="utf-8",
        )
        apply_icon("stock-app.desktop", str(shadow_launcher), str(self.sample_png))
        shadow_icon = Path(load_state()["stock-app.desktop"]["icon_path"])

        with patch("glyph.overrides.find_stock", side_effect=lambda did: str(stock_file) if did == "stock-app.desktop" else ""):
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
            manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
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


if __name__ == "__main__":
    unittest.main()
