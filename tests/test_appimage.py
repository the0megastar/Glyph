import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glyph import appimage, overrides
from glyph.appimage import (
    AppImageError,
    is_regular_appimage,
    plan_appimage_registration,
    register_appimage,
    unregister_appimage,
    discover_unregistered_appimages,
)


class TestAppImageIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="glyph-appimage-test-")
        self.root = Path(self.tmp.name)
        self.user_apps = self.root / "Applications"
        self.desktop_apps = self.root / "local-applications"
        self.data_dir = self.root / "glyph"
        self.icons_dir = self.data_dir / "icons"
        self.state_file = self.data_dir / "overrides.json"

        self.user_apps.mkdir(parents=True)
        self.desktop_apps.mkdir(parents=True)
        self.icons_dir.mkdir(parents=True)

        self.patchers = [
            patch.object(appimage, "USER_APPLICATIONS_DIR", self.user_apps),
            patch.object(appimage, "APPLICATIONS_DIR", self.desktop_apps),
            patch.object(overrides, "APPLICATIONS_DIR", self.desktop_apps),
            patch.object(overrides, "DATA_DIR", self.data_dir),
            patch.object(overrides, "ICONS_DIR", self.icons_dir),
            patch.object(appimage, "ICONS_DIR", self.icons_dir),
            patch.object(overrides, "STATE_FILE", self.state_file),
            patch.object(overrides, "refresh_desktop_database"),
            patch.object(appimage, "refresh_desktop_database"),
        ]
        for p in self.patchers:
            p.start()
        overrides._ensure_dirs()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.tmp.cleanup()

    def create_fake_appimage(self, name: str, dir_path: Path | None = None) -> Path:
        target_dir = dir_path or (self.root / "Downloads")
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / name
        # Write valid ELF header with AppImage Type 2 magic
        header = b"\x7fELF" + b"\x02\x01\x01\x00" + b"AI\x02" + b"\x00" * 2000
        file_path.write_bytes(header)
        return file_path

    def test_is_regular_appimage(self):
        valid = self.create_fake_appimage("test.AppImage")
        self.assertTrue(is_regular_appimage(valid))

        # Not an ELF
        non_elf = self.root / "test.txt"
        non_elf.write_text("not an elf", encoding="utf-8")
        self.assertFalse(is_regular_appimage(non_elf))

        # Nonexistent
        self.assertFalse(is_regular_appimage(self.root / "missing.AppImage"))

    def test_plan_and_register_external_appimage(self):
        source = self.create_fake_appimage("Wordcraft-0.5.0-x86_64.AppImage")
        plan = plan_appimage_registration(source)
        self.assertFalse(plan.is_in_place)
        self.assertEqual(plan.managed_file.parent, self.user_apps)
        self.assertEqual(plan.desktop_id, "appimage-wordcraft.desktop")

        desktop_id = register_appimage(plan, "Wordcraft")
        self.assertEqual(desktop_id, "appimage-wordcraft.desktop")

        # Verify source file is preserved untouched
        self.assertTrue(source.is_file(), "Original AppImage must remain untouched")
        # Verify managed copy was created
        self.assertTrue(plan.managed_file.is_file())
        self.assertTrue(os.access(plan.managed_file, os.X_OK))

        # Verify per-user launcher was created
        launcher_file = self.desktop_apps / desktop_id
        self.assertTrue(launcher_file.is_file())
        text = launcher_file.read_text(encoding="utf-8")
        self.assertIn("Name=Wordcraft", text)
        self.assertIn(str(plan.managed_file), text)
        self.assertIn("X-Glyph-Managed=true", text)

        # Verify recorded in state
        state = overrides.load_state()
        self.assertIn(desktop_id, state)
        self.assertTrue(state[desktop_id].get("glyph_managed_appimage"))
        self.assertEqual(state[desktop_id].get("appimage_source"), str(source))
        self.assertEqual(state[desktop_id].get("appimage_managed"), str(plan.managed_file))

    def test_register_in_place_appimage(self):
        in_place_file = self.create_fake_appimage("InPlace.AppImage", dir_path=self.user_apps)
        plan = plan_appimage_registration(in_place_file)
        self.assertTrue(plan.is_in_place)
        self.assertEqual(plan.managed_file, in_place_file)

        desktop_id = register_appimage(plan, "In Place App")
        self.assertTrue((self.desktop_apps / desktop_id).is_file())

    def test_unregister_preserves_binaries(self):
        source = self.create_fake_appimage("KeepMe.AppImage")
        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Keep Me")

        launcher_file = self.desktop_apps / desktop_id
        self.assertTrue(launcher_file.is_file())
        self.assertTrue(plan.managed_file.is_file())

        unregister_appimage(desktop_id)

        # Launcher is removed
        self.assertFalse(launcher_file.exists())
        # Both original and managed binary remain
        self.assertTrue(source.is_file(), "Original binary must be preserved on unregister")
        self.assertTrue(plan.managed_file.is_file(), "Managed binary must be preserved on unregister")
        # State record removed
        self.assertNotIn(desktop_id, overrides.load_state())

    def test_discover_unregistered_appimages(self):
        img1 = self.create_fake_appimage("App1.AppImage", dir_path=self.user_apps)
        img2 = self.create_fake_appimage("App2.AppImage", dir_path=self.user_apps)

        # Existing command already covers img1
        existing_commands = {str(img1), f'"{img1}" %U'}
        unregistered = discover_unregistered_appimages(existing_commands)

        self.assertIn(img2, unregistered)
        self.assertNotIn(img1, unregistered)


    def test_planning_is_strictly_readonly(self):
        nonexistent_apps = self.root / "NonexistentApplications"
        source = self.create_fake_appimage("ReadonlyTest.AppImage")
        with patch.object(appimage, "USER_APPLICATIONS_DIR", nonexistent_apps):
            plan = plan_appimage_registration(source)
            self.assertFalse(nonexistent_apps.exists(), "Planning must not create destination directory")
            self.assertEqual(plan.display_name, "ReadonlyTest")
            self.assertFalse(plan.managed_file.exists())
            self.assertFalse(plan.launcher_path.exists())

    def test_managed_collision_detection(self):
        source = self.create_fake_appimage("Collision.AppImage")
        existing_managed = self.user_apps / "Collision.AppImage"
        existing_managed.write_bytes(b"existing binary content")

        plan = plan_appimage_registration(source)
        self.assertTrue(plan.managed_collision)
        self.assertTrue(plan.has_collision)

        with self.assertRaises(AppImageError) as ctx:
            register_appimage(plan)
        self.assertIn("already exists", str(ctx.exception))
        self.assertEqual(existing_managed.read_bytes(), b"existing binary content")

    def test_launcher_collision_detection(self):
        source = self.create_fake_appimage("LauncherCollision.AppImage")
        existing_launcher = self.desktop_apps / "appimage-launchercollision.desktop"
        existing_launcher.write_text("existing launcher", encoding="utf-8")

        plan = plan_appimage_registration(source)
        self.assertTrue(plan.launcher_collision)
        self.assertTrue(plan.has_collision)

        with self.assertRaises(AppImageError) as ctx:
            register_appimage(plan)
        self.assertIn("already exists", str(ctx.exception))
        self.assertEqual(existing_launcher.read_text(encoding="utf-8"), "existing launcher")

    def test_unregister_detects_externally_modified_launcher(self):
        source = self.create_fake_appimage("ModifiedLauncher.AppImage")
        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Modified App")

        launcher_file = self.desktop_apps / desktop_id
        launcher_file.write_text("[Desktop Entry]\nModified=True\n", encoding="utf-8")

        with self.assertRaises(AppImageError) as ctx:
            unregister_appimage(desktop_id)
        self.assertIn("modified externally", str(ctx.exception))

        self.assertTrue(launcher_file.is_file())
        self.assertTrue(plan.managed_file.is_file())
        self.assertIn(desktop_id, overrides.load_state())

    def test_unregister_detects_externally_modified_icon(self):
        source = self.create_fake_appimage("ModifiedIcon.AppImage")
        custom_icon = self.root / "custom.png"
        custom_icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Icon App", custom_icon_path=str(custom_icon))

        state = overrides.load_state()
        icon_path = Path(state[desktop_id]["icon_path"])
        self.assertTrue(icon_path.is_file())

        icon_path.write_bytes(b"corrupted icon bytes")

        res = unregister_appimage(desktop_id)
        self.assertTrue(res.get("icon_skipped"))

        # Modified icon was preserved on disk
        self.assertTrue(icon_path.is_file())
        self.assertEqual(icon_path.read_bytes(), b"corrupted icon bytes")
        # Unmodified launcher was cleanly unregistered
        self.assertFalse((self.desktop_apps / desktop_id).is_file())
        self.assertNotIn(desktop_id, overrides.load_state())

    def test_unregister_rejects_symlink_launcher(self):
        source = self.create_fake_appimage("SymlinkTest.AppImage")
        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Symlink App")

        launcher_file = self.desktop_apps / desktop_id
        launcher_file.unlink()
        target_outside = self.root / "outside.desktop"
        target_outside.write_text("outside", encoding="utf-8")
        launcher_file.symlink_to(target_outside)

        with self.assertRaises((AppImageError, overrides.OverrideError)):
            unregister_appimage(desktop_id)
        self.assertTrue(target_outside.is_file())

    def test_register_appimage_in_flatpak_uses_host_exec(self):
        source = self.create_fake_appimage("FlatpakTest.AppImage")
        plan = plan_appimage_registration(source)
        host_apps = Path("/home/hostuser/Applications")
        with patch("glyph.appimage.paths.in_flatpak", return_value=True), patch(
            "glyph.appimage.paths.host_applications_dir", return_value=host_apps
        ):
            desktop_id = register_appimage(plan, "Flatpak App")
            launcher_file = self.desktop_apps / desktop_id
            self.assertTrue(launcher_file.is_file())
            content = launcher_file.read_text(encoding="utf-8")
            self.assertIn('Exec="/home/hostuser/Applications/FlatpakTest.AppImage" %U', content)

    def test_unregister_transaction_failure_restores_state_and_files(self):
        source = self.create_fake_appimage("RollbackTest.AppImage")
        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Rollback App")

        launcher_file = self.desktop_apps / desktop_id
        self.assertTrue(launcher_file.is_file())

        with patch("glyph.appimage._commit", side_effect=RuntimeError("Disk failure")):
            with self.assertRaises(RuntimeError):
                unregister_appimage(desktop_id)

        self.assertTrue(launcher_file.is_file())
        self.assertIn(desktop_id, overrides.load_state())

    def test_register_commit_failure_cleans_up_copied_binary(self):
        source = self.create_fake_appimage("CommitFail.AppImage")
        plan = plan_appimage_registration(source)

        with patch("glyph.appimage._commit", side_effect=PermissionError("Permission denied")):
            with self.assertRaises((PermissionError, overrides.OverrideError)):
                register_appimage(plan, "Fail App")

        # Managed copy must be cleaned up
        self.assertFalse(plan.managed_file.exists(), "Managed copy must be removed on commit failure")
        self.assertFalse((self.desktop_apps / plan.desktop_id).exists())
        self.assertNotIn(plan.desktop_id, overrides.load_state())


    def test_bounded_extraction_stdout_limit(self):
        # Python command outputting 2 MiB of data with a 1 MiB limit
        cmd = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'A' * (2 * 1024 * 1024))"]
        with self.assertRaises(appimage.ExtractorOutputLimitExceeded):
            appimage._run_bounded_extraction(cmd, max_stdout=1024 * 1024, timeout=3.0)

    def test_bounded_extraction_exact_limit_and_one_byte_over(self):
        # 1. Exact stdout limit succeeds
        cmd_ok = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'X' * 1000)"]
        code, out, err = appimage._run_bounded_extraction(cmd_ok, max_stdout=1000, timeout=3.0)
        self.assertEqual(code, 0)
        self.assertEqual(len(out), 1000)

        # 2. Outputting one byte over limit stops/reaps child and raises ExtractorOutputLimitExceeded
        cmd_over = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'X' * 1001)"]
        with self.assertRaises(appimage.ExtractorOutputLimitExceeded):
            appimage._run_bounded_extraction(cmd_over, max_stdout=1000, timeout=3.0)

        # 3. Exact stderr limit succeeds
        cmd_err_ok = [sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'Y' * 500)"]
        code, out, err = appimage._run_bounded_extraction(cmd_err_ok, max_stdout=1000, max_stderr=500, timeout=3.0)
        self.assertEqual(code, 0)
        self.assertEqual(len(err), 500)

        # 4. Outputting one byte over stderr limit raises ExtractorOutputLimitExceeded
        cmd_err_over = [sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'Y' * 501)"]
        with self.assertRaises(appimage.ExtractorOutputLimitExceeded):
            appimage._run_bounded_extraction(cmd_err_over, max_stdout=1000, max_stderr=500, timeout=3.0)

    def test_bounded_extraction_nonzero_exit(self):
        # Nonzero exit returns returncode cleanly without crashing
        cmd = [sys.executable, "-c", "import sys; sys.stderr.write('extractor error'); sys.exit(2)"]
        code, out, err = appimage._run_bounded_extraction(cmd, max_stdout=1000, max_stderr=1000, timeout=3.0)
        self.assertEqual(code, 2)
        self.assertEqual(err, b"extractor error")

    def test_bounded_extraction_stderr_limit(self):
        # Python command outputting 128 KiB to stderr with a 64 KiB limit
        cmd = [sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'E' * (128 * 1024))"]
        with self.assertRaises(appimage.ExtractorOutputLimitExceeded):
            appimage._run_bounded_extraction(cmd, max_stdout=1024 * 1024, max_stderr=64 * 1024, timeout=3.0)

    def test_bounded_extraction_timeout(self):
        # Command sleeping for longer than timeout
        cmd = [sys.executable, "-c", "import time; time.sleep(2.0)"]
        with self.assertRaises(subprocess.TimeoutExpired):
            appimage._run_bounded_extraction(cmd, max_stdout=1024 * 1024, timeout=0.2)

    def test_inspect_appimage_invalid_utf8_handled(self):
        source = self.create_fake_appimage("InvalidUTF8.AppImage")
        with patch("glyph.appimage.shutil.which", return_value="/usr/bin/7z"), \
             patch("glyph.appimage._run_bounded_extraction") as mock_extract:
            # Return invalid UTF-8 bytes for desktop entry
            mock_extract.return_value = (0, b"\xff\xfe\x00\x00Not valid UTF-8", b"")
            meta = appimage.inspect_appimage(source)
            self.assertEqual(meta.name, "InvalidUTF8")
            self.assertEqual(meta.comment, "")

    def test_slugify_name_and_derive_desktop_id(self):
        from glyph.launcher import is_valid_desktop_id
        cases = [
            ("Café", "cafe", "appimage-cafe.desktop"),
            ("北京", "app", "appimage-app.desktop"),
            ("My App (v2.0)!", "my-app-v2-0", "appimage-my-app-v2-0.desktop"),
            ("---", "app", "appimage-app.desktop"),
            ("", "app", "appimage-app.desktop"),
            ("Already-ASCII", "already-ascii", "appimage-already-ascii.desktop"),
            ("Mixed Español & Deutsch: Größe", "mixed-espanol-deutsch-groe", "appimage-mixed-espanol-deutsch-groe.desktop"),
        ]
        for raw, expected_slug, expected_id in cases:
            with self.subTest(raw=raw):
                slug = appimage.slugify_name(raw)
                self.assertEqual(slug, expected_slug)
                desktop_id = appimage.derive_desktop_id(raw)
                self.assertEqual(desktop_id, expected_id)
                self.assertTrue(is_valid_desktop_id(desktop_id))
                self.assertEqual(overrides._id(desktop_id), desktop_id)

    def test_derive_desktop_id_collision_fallback(self):
        source = self.create_fake_appimage("CollisionTest.AppImage")
        existing = {"appimage-collisiontest.desktop"}
        id_fallback = appimage.derive_desktop_id("CollisionTest", appimage_path=source, existing_ids=existing)
        self.assertTrue(id_fallback.startswith("appimage-collisiontest-"))
        self.assertTrue(id_fallback.endswith(".desktop"))
        self.assertNotEqual(id_fallback, "appimage-collisiontest.desktop")

        # Second collision appends -2
        existing.add(id_fallback)
        id_fallback_2 = appimage.derive_desktop_id("CollisionTest", appimage_path=source, existing_ids=existing)
        self.assertEqual(id_fallback_2, id_fallback.removesuffix(".desktop") + "-2.desktop")

    def test_register_appimage_unicode_preserves_display_name(self):
        source = self.create_fake_appimage("UnicodeDisplay.AppImage")
        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Café ☕ Deluxe")
        launcher_file = self.desktop_apps / desktop_id
        content = launcher_file.read_text(encoding="utf-8")
        self.assertIn("Name=Café ☕ Deluxe", content)
        self.assertEqual(desktop_id, "appimage-cafe-deluxe.desktop")

    def test_register_appimage_stores_matching_icon_hash(self):
        source = self.create_fake_appimage("HashedIcon.AppImage")
        custom_icon = self.root / "icon.png"
        icon_bytes = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64
        custom_icon.write_bytes(icon_bytes)

        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Hashed App", custom_icon_path=str(custom_icon))

        state = overrides.load_state()
        record = state[desktop_id]
        import hashlib
        self.assertEqual(record["icon_hash"], hashlib.sha256(icon_bytes).hexdigest())
        self.assertTrue(Path(record["icon_path"]).is_file())

    def test_unregister_preserves_icon_with_missing_expected_hash(self):
        source = self.create_fake_appimage("NoHashIcon.AppImage")
        custom_icon = self.root / "nohash.png"
        custom_icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x02" * 64)

        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "No Hash App", custom_icon_path=str(custom_icon))

        # Remove icon_hash to simulate missing expected hash
        state = overrides.load_state()
        state[desktop_id]["icon_hash"] = ""
        overrides._commit(state, {})

        icon_path = Path(state[desktop_id]["icon_path"])
        self.assertTrue(icon_path.is_file())

        res = unregister_appimage(desktop_id)
        self.assertTrue(res.get("icon_skipped"))
        # Icon was preserved
        self.assertTrue(icon_path.is_file())
        # Launcher was removed
        self.assertFalse((self.desktop_apps / desktop_id).is_file())
        self.assertNotIn(desktop_id, overrides.load_state())

    def test_discover_registration_candidates(self):
        # 1. Unregistered regular AppImage in user_apps
        cand_a = self.create_fake_appimage("CandidateA.AppImage")
        cand_a_target = self.user_apps / "CandidateA.AppImage"
        cand_a.rename(cand_a_target)

        # 2. Registered AppImage in user_apps
        reg_b = self.create_fake_appimage("RegisteredB.AppImage")
        reg_b_target = self.user_apps / "RegisteredB.AppImage"
        reg_b.rename(reg_b_target)

        # 3. Symlink to registered AppImage
        symlink_b = self.user_apps / "LinkB.AppImage"
        symlink_b.symlink_to(reg_b_target)

        # 4. Hidden file, directory, broken symlink, non-ELF text file
        (self.user_apps / ".Hidden.AppImage").write_bytes(b"\x7fELF" + b"\x00" * 2000)
        (self.user_apps / "Folder.AppImage").mkdir()
        (self.user_apps / "Broken.AppImage").symlink_to(self.root / "nonexistent")
        (self.user_apps / "Text.AppImage").write_text("not an elf executable", encoding="utf-8")

        existing_targets = {str(reg_b_target.resolve()), str(reg_b_target)}
        candidates = appimage.discover_registration_candidates(existing_targets)

        # Only CandidateA should be discovered
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].path, cand_a_target)
        self.assertEqual(candidates[0].resolved_path, cand_a_target.resolve())
        self.assertEqual(candidates[0].display_name, "CandidateA")

    def test_unregister_removes_unmodified_matching_icon(self):
        source = self.create_fake_appimage("MatchingIcon.AppImage")
        custom_icon = self.root / "matching.png"
        custom_icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x03" * 64)

        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Matching App", custom_icon_path=str(custom_icon))

        state = overrides.load_state()
        icon_path = Path(state[desktop_id]["icon_path"])
        self.assertTrue(icon_path.is_file())

        res = unregister_appimage(desktop_id)
        self.assertFalse(res.get("icon_skipped"))
        # Unmodified icon was cleanly deleted
        self.assertFalse(icon_path.exists())
        self.assertFalse((self.desktop_apps / desktop_id).is_file())
        self.assertNotIn(desktop_id, overrides.load_state())

    def test_unregister_preserves_untracked_icon(self):
        source = self.create_fake_appimage("NoIcon.AppImage")
        plan = plan_appimage_registration(source)
        # Register with no embedded or custom icon
        desktop_id = register_appimage(plan, "No Icon App")

        state = overrides.load_state()
        self.assertEqual(state[desktop_id].get("icon_path", ""), "")

        res = unregister_appimage(desktop_id)
        self.assertFalse(res.get("icon_skipped"))
        self.assertFalse((self.desktop_apps / desktop_id).is_file())
        self.assertNotIn(desktop_id, overrides.load_state())

    def test_replace_icon_updates_path_and_hash_consistently(self):
        source = self.create_fake_appimage("ReplaceIcon.AppImage")
        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Replace App")

        launcher_path = self.desktop_apps / desktop_id
        icon_v1 = self.root / "icon_v1.png"
        v1_bytes = b"\x89PNG\r\n\x1a\n" + b"\x10" * 64
        icon_v1.write_bytes(v1_bytes)

        overrides.apply_icon(desktop_id, str(launcher_path), str(icon_v1))
        state1 = overrides.load_state()
        self.assertEqual(state1[desktop_id]["icon_hash"], hashlib.sha256(v1_bytes).hexdigest())
        path1 = Path(state1[desktop_id]["icon_path"])
        self.assertTrue(path1.is_file())

        # Replace with icon_v2
        icon_v2 = self.root / "icon_v2.png"
        v2_bytes = b"\x89PNG\r\n\x1a\n" + b"\x20" * 64
        icon_v2.write_bytes(v2_bytes)

        overrides.apply_icon(desktop_id, str(launcher_path), str(icon_v2))
        state2 = overrides.load_state()
        self.assertEqual(state2[desktop_id]["icon_hash"], hashlib.sha256(v2_bytes).hexdigest())
        path2 = Path(state2[desktop_id]["icon_path"])
        self.assertTrue(path2.is_file())
        self.assertNotEqual(path1, path2)
        # Old unmodified icon was safely cleaned up
        self.assertFalse(path1.exists())

    def test_discover_registration_candidates_deterministic_order(self):
        for name in ("Zeta.AppImage", "alpha.AppImage", "Beta.AppImage"):
            img = self.create_fake_appimage(name)
            img.rename(self.user_apps / name)

        candidates = appimage.discover_registration_candidates(set())
        names = [c.display_name for c in candidates]
        self.assertEqual(names, ["alpha", "Beta", "Zeta"])

    def test_registration_custom_icon_validation_fails_before_binary_copy(self):
        source = self.create_fake_appimage("InvalidIcon.AppImage")
        invalid_icon = self.root / "corrupt.png"
        invalid_icon.write_bytes(b"not a valid png file")

        plan = plan_appimage_registration(source)
        with self.assertRaises(AppImageError):
            register_appimage(plan, "Invalid Icon App", custom_icon_path=str(invalid_icon))

        # Binary was never copied to ~/Applications
        self.assertFalse(plan.managed_file.exists())
        # Source file is preserved untouched
        self.assertTrue(source.is_file())
        # No launcher was created
        self.assertFalse((self.desktop_apps / plan.desktop_id).exists())
        # State was not altered
        self.assertNotIn(plan.desktop_id, overrides.load_state())

    def test_registration_state_loading_fails_before_binary_copy(self):
        source = self.create_fake_appimage("StateFail.AppImage")
        plan = plan_appimage_registration(source)

        with patch("glyph.appimage.load_state", side_effect=overrides.OverrideError("corrupted state")):
            with self.assertRaises(overrides.OverrideError):
                register_appimage(plan, "State Fail App")

        # Binary was never copied to ~/Applications
        self.assertFalse(plan.managed_file.exists())
        self.assertTrue(source.is_file())

    def test_registration_serialization_fails_before_binary_copy(self):
        source = self.create_fake_appimage("SerialFail.AppImage")
        plan = plan_appimage_registration(source)

        with patch("gi.repository.GLib.KeyFile.to_data", side_effect=Exception("KeyFile serialization error")):
            with self.assertRaises(AppImageError):
                register_appimage(plan, "Serial Fail App")

        # Binary was never copied to ~/Applications
        self.assertFalse(plan.managed_file.exists())
        self.assertTrue(source.is_file())

    def test_registration_commit_fails_cleans_up_managed_binary(self):
        source = self.create_fake_appimage("CommitFail.AppImage")
        plan = plan_appimage_registration(source)

        with patch("glyph.appimage._commit", side_effect=overrides.OverrideError("commit disk error")):
            with self.assertRaises(overrides.OverrideError):
                register_appimage(plan, "Commit Fail App")

        # Newly created managed copy in ~/Applications was cleaned up
        self.assertFalse(plan.managed_file.exists(), "Managed copy must be removed on commit failure")
        # Source file is preserved untouched
        self.assertTrue(source.is_file())
        # No launcher exists
        self.assertFalse((self.desktop_apps / plan.desktop_id).exists())
        # State was not altered
        self.assertNotIn(plan.desktop_id, overrides.load_state())

    def test_inplace_registration_chmod_failure(self):
        inplace_source = self.create_fake_appimage("InPlaceFail.AppImage", dir_path=self.user_apps)
        plan = plan_appimage_registration(inplace_source)
        self.assertTrue(plan.is_in_place)

        with patch("os.chmod", side_effect=OSError("Permission denied")):
            with self.assertRaisesRegex(AppImageError, "Failed to make AppImage executable"):
                register_appimage(plan, "In-Place Fail App")

        # Source file was not removed
        self.assertTrue(inplace_source.is_file())
        # No launcher was created
        self.assertFalse((self.desktop_apps / plan.desktop_id).exists())
        # State was not altered
        self.assertNotIn(plan.desktop_id, overrides.load_state())

    def test_unregister_with_keep_artwork_preserves_icon(self):
        source = self.create_fake_appimage("KeepIcon.AppImage")
        custom_icon = self.root / "keep.png"
        custom_icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x55" * 64)

        plan = plan_appimage_registration(source)
        desktop_id = register_appimage(plan, "Keep Icon App", custom_icon_path=str(custom_icon))

        state = overrides.load_state()
        icon_path = Path(state[desktop_id]["icon_path"])
        self.assertTrue(icon_path.is_file())

        res = unregister_appimage(desktop_id, delete_icon=False)
        self.assertFalse(res.get("icon_skipped"))
        # Kept icon remains in ICONS_DIR
        self.assertTrue(icon_path.is_file())
        # Launcher and state are removed
        self.assertFalse((self.desktop_apps / desktop_id).is_file())
        self.assertNotIn(desktop_id, overrides.load_state())


if __name__ == "__main__":
    unittest.main()


