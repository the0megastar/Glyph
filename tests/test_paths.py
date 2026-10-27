import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glyph.paths import (
    data_home,
    desktop_index,
    find_stock,
    host_applications_dir,
    host_data_home,
    host_user_home,
    in_flatpak,
    local_applications_dir,
    stock_dirs,
    user_application_dirs,
)


class TestPaths(unittest.TestCase):
    def test_flatpak_customization_updates_host_launcher(self):
        # Fresh import verifies the real module-level destinations, not patched ones.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / 'private'
            env = dict(os.environ, HOME=tmp, XDG_DATA_HOME=str(private),
                       HOST_XDG_DATA_HOME=str(root / 'host'), XDG_DATA_DIRS=str(root / 'shared'),
                       PYTHONPATH=str(Path(__file__).resolve().parent.parent / 'src'))
            result = subprocess.run([sys.executable, '-B', '-c', '''
from pathlib import Path
from unittest.mock import patch
from glyph import paths
with patch.object(paths, 'in_flatpak', return_value=True):
    from glyph import overrides as ov
    from glyph import catalog
    # In Flatpak, the host applications directory is mounted at $XDG_DATA_HOME/applications
    apps_dir = paths.data_home() / 'applications'
    apps_dir.mkdir(parents=True)
    launcher = apps_dir / 'demo.desktop'
    original = '[Desktop Entry]\\nType=Application\\nName=Original\\nExec=/bin/true\\n'
    launcher.write_text(original)
    assert ov.APPLICATIONS_DIR == apps_dir
    with patch.object(ov, 'refresh_desktop_database'):
        ov.apply_name('demo.desktop', str(launcher), 'Changed')
        assert 'Name=Changed' in launcher.read_text()
        apps = {app.desktop_id: app for app in catalog.list_apps(ov.load_state())}
        assert apps['demo.desktop'].name == 'Changed'
        ov.revert_name('demo.desktop')
    assert sorted(launcher.read_text().splitlines()) == sorted(original.splitlines())
'''], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_flatpak_host_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            host = home / 'custom-data'
            private = home / '.var/app/glyph/data'
            export = private / 'flatpak/exports/share/applications'
            export.mkdir(parents=True)
            (export / 'demo.desktop').write_text('[Desktop Entry]\nName=Demo\n')
            with patch('glyph.paths.in_flatpak', return_value=True), patch.object(
                    Path, 'home', return_value=home), patch.dict(os.environ, {
                        'XDG_DATA_HOME': str(private), 'HOST_XDG_DATA_HOME': str(host),
                        'XDG_DATA_DIRS': '/app/share:/usr/share'}):
                self.assertEqual(user_application_dirs(), [private / 'applications'])
                self.assertEqual(desktop_index(stock_dirs())['demo.desktop'], export / 'demo.desktop')
                for value in ('', 'relative'):
                    with patch.dict(os.environ, {'HOST_XDG_DATA_HOME': value}):
                        self.assertEqual(host_data_home(), home / '.local/share')

    def test_data_home_default(self):
        old = os.environ.get("XDG_DATA_HOME")
        try:
            os.environ.pop("XDG_DATA_HOME", None)
            self.assertEqual(data_home(), Path.home() / ".local/share")
        finally:
            if old is not None:
                os.environ["XDG_DATA_HOME"] = old

    def test_data_home_custom_absolute(self):
        old = os.environ.get("XDG_DATA_HOME")
        try:
            os.environ["XDG_DATA_HOME"] = "/tmp/custom_xdg_data"
            self.assertEqual(data_home(), Path("/tmp/custom_xdg_data"))
        finally:
            if old is not None:
                os.environ["XDG_DATA_HOME"] = old
            else:
                os.environ.pop("XDG_DATA_HOME", None)

    def test_data_home_relative_ignored(self):
        old = os.environ.get("XDG_DATA_HOME")
        try:
            os.environ["XDG_DATA_HOME"] = "relative/path"
            self.assertEqual(data_home(), Path.home() / ".local/share")
        finally:
            if old is not None:
                os.environ["XDG_DATA_HOME"] = old
            else:
                os.environ.pop("XDG_DATA_HOME", None)

    def test_in_flatpak(self):
        # On standard test runners outside a container, /.flatpak-info is typically absent
        expected = Path("/.flatpak-info").is_file()
        self.assertEqual(in_flatpak(), expected)

    def test_stock_dirs_returns_paths(self):
        dirs = stock_dirs()
        self.assertIsInstance(dirs, list)
        self.assertTrue(all(isinstance(p, Path) for p in dirs))
        # Stock dirs should end with applications
        self.assertTrue(all(p.name == "applications" for p in dirs))

    def test_desktop_index_flat_and_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flat.desktop").write_text("[Desktop Entry]\nName=Flat\n", encoding="utf-8")
            sub = root / "category"
            sub.mkdir()
            (sub / "nested.desktop").write_text("[Desktop Entry]\nName=Nested\n", encoding="utf-8")
            deep = root / "a" / "b"
            deep.mkdir(parents=True)
            (deep / "deep.desktop").write_text("[Desktop Entry]\nName=Deep\n", encoding="utf-8")

            index = desktop_index([root])
            self.assertIn("flat.desktop", index)
            self.assertEqual(index["flat.desktop"], root / "flat.desktop")

            self.assertIn("category-nested.desktop", index)
            self.assertEqual(index["category-nested.desktop"], sub / "nested.desktop")

            self.assertIn("a-b-deep.desktop", index)
            self.assertEqual(index["a-b-deep.desktop"], deep / "deep.desktop")

    def test_desktop_index_root_priority(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            r1 = Path(tmp1)
            r2 = Path(tmp2)
            (r1 / "app.desktop").write_text("[Desktop Entry]\nName=Root1\n", encoding="utf-8")
            (r2 / "app.desktop").write_text("[Desktop Entry]\nName=Root2\n", encoding="utf-8")

            index = desktop_index([r1, r2])
            self.assertEqual(index["app.desktop"], r1 / "app.desktop")

            index_reverse = desktop_index([r2, r1])
            self.assertEqual(index_reverse["app.desktop"], r2 / "app.desktop")

    def test_desktop_index_flat_preference_over_nested_same_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Both yield ID 'foo-bar.desktop'
            (root / "foo-bar.desktop").write_text("[Desktop Entry]\nName=Flat\n", encoding="utf-8")
            sub = root / "foo"
            sub.mkdir()
            (sub / "bar.desktop").write_text("[Desktop Entry]\nName=Nested\n", encoding="utf-8")

            index = desktop_index([root])
            self.assertEqual(index["foo-bar.desktop"], root / "foo-bar.desktop")

    def test_empty_xdg_data_dirs_uses_spec_defaults(self):
        with patch.dict(os.environ, {"XDG_DATA_DIRS": ""}), patch("glyph.paths.in_flatpak", return_value=False):
            dirs = stock_dirs()
            self.assertEqual(dirs[:2], [Path("/usr/local/share/applications"), Path("/usr/share/applications")])

    def test_find_stock_with_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "app.desktop"
            launcher.write_text("[Desktop Entry]\nName=Test\n", encoding="utf-8")
            custom_index = {"app.desktop": launcher}
            self.assertEqual(find_stock("app.desktop", index=custom_index), str(launcher))
            self.assertEqual(find_stock("missing.desktop", index=custom_index), "")

    def test_host_user_home_native(self):
        with patch("glyph.paths.in_flatpak", return_value=False):
            self.assertEqual(host_user_home(), Path.home())

    def test_host_user_home_flatpak_via_env(self):
        with patch("glyph.paths.in_flatpak", return_value=True), patch.dict(
            os.environ, {"HOST_XDG_DATA_HOME": "/home/testuser/.local/share"}
        ):
            self.assertEqual(host_user_home(), Path("/home/testuser"))

    def test_host_user_home_flatpak_via_spawn(self):
        with patch("glyph.paths.in_flatpak", return_value=True), patch.dict(
            os.environ, {"HOST_XDG_DATA_HOME": ""}
        ), patch("glyph.paths.shutil.which", return_value="/usr/bin/flatpak-spawn"), patch(
            "glyph.paths.subprocess.run",
            return_value=unittest.mock.MagicMock(returncode=0, stdout="/home/spawnuser\n"),
        ) as mock_run_fn:
            self.assertEqual(host_user_home(), Path("/home/spawnuser"))
            self.assertEqual(
                mock_run_fn.call_args[0][0],
                ["/usr/bin/flatpak-spawn", "--host", "printenv", "HOME"],
            )

    def test_applications_dirs(self):
        with patch("glyph.paths.host_user_home", return_value=Path("/home/custom")):
            self.assertEqual(host_applications_dir(), Path("/home/custom/Applications"))
        self.assertEqual(local_applications_dir(), Path.home() / "Applications")

    def test_flatpak_icon_dirs_includes_host_xdg_and_icons(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            host_dh = home / "host_local_share"
            host_icons = host_dh / "icons"
            host_icons.mkdir(parents=True)
            with patch("glyph.paths.in_flatpak", return_value=True), \
                 patch("glyph.paths.host_data_home", return_value=host_dh), \
                 patch("glyph.paths.host_user_home", return_value=home), \
                 patch.object(Path, "home", return_value=home):
                from glyph.paths import icon_dirs
                dirs = icon_dirs()
                self.assertIn(host_icons, dirs)
                self.assertIn(home / ".icons", dirs)

    def test_native_icon_dirs_preserves_standard_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("glyph.paths.in_flatpak", return_value=False), \
                 patch.object(Path, "home", return_value=home):
                from glyph.paths import icon_dirs, data_home
                dirs = icon_dirs()
                self.assertIn(data_home() / "icons", dirs)
                self.assertIn(data_home() / "pixmaps", dirs)
                self.assertNotIn(home / ".icons", dirs)

    def test_manifest_permissions_review(self):
        manifest_path = Path(__file__).resolve().parent.parent / "io.github.the0megastar.Glyph.yaml"
        if not manifest_path.is_file():
            self.skipTest("Manifest file not present in build root")
        content = manifest_path.read_text(encoding="utf-8")
        try:
            import yaml
            manifest = yaml.safe_load(content)
            finish_args = manifest.get("finish-args", [])
        except ImportError:
            finish_args = []
            in_finish_args = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped == "finish-args:":
                    in_finish_args = True
                    continue
                if in_finish_args:
                    if stripped.startswith("- "):
                        finish_args.append(stripped[2:].strip().strip('"').strip("'"))
                    elif stripped and not stripped.startswith("#"):
                        break
        self.assertIn("--filesystem=xdg-data/icons:ro", finish_args)
        self.assertIn("--filesystem=~/.icons:ro", finish_args)
        # Ensure no write access is granted to icon folders
        for arg in finish_args:
            if "icons" in arg:
                self.assertTrue(arg.endswith(":ro"), f"Icon mount must be strictly read-only: {arg}")


if __name__ == "__main__":
    unittest.main()

