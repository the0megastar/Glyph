import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glyph.paths import data_home, desktop_index, find_stock, host_data_home, in_flatpak, stock_dirs, user_application_dirs


class TestPaths(unittest.TestCase):
    def test_flatpak_customization_updates_host_launcher(self):
        # Fresh import verifies the real module-level destinations, not patched ones.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ, HOME=tmp, XDG_DATA_HOME=str(root / 'private'),
                       HOST_XDG_DATA_HOME=str(root / 'host'), XDG_DATA_DIRS=str(root / 'shared'),
                       PYTHONPATH=str(Path(__file__).resolve().parent.parent / 'src'))
            result = subprocess.run([sys.executable, '-B', '-c', '''
from pathlib import Path
from unittest.mock import patch
from glyph import paths
with patch.object(paths, 'in_flatpak', return_value=True):
    from glyph import overrides as ov
    from glyph import catalog
    host = paths.host_data_home() / 'applications'
    host.mkdir(parents=True)
    launcher = host / 'demo.desktop'
    original = '[Desktop Entry]\\nType=Application\\nName=Original\\nExec=/bin/true\\n'
    launcher.write_text(original)
    assert ov.APPLICATIONS_DIR == host
    with patch.object(ov, 'refresh_desktop_database'):
        ov.apply_name('demo.desktop', str(launcher), 'Changed')
        assert 'Name=Changed' in launcher.read_text()
        apps = {app.desktop_id: app for app in catalog.list_apps(ov.load_state())}
        assert apps['demo.desktop'].name == 'Changed'
        ov.revert_name('demo.desktop')
    assert sorted(launcher.read_text().splitlines()) == sorted(original.splitlines())
    assert not (paths.data_home() / 'applications/demo.desktop').exists()
'''], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_flatpak_host_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            host = home / 'custom-data'
            private = home / '.var/app/glyph/data'
            export = host / 'flatpak/exports/share/applications'
            export.mkdir(parents=True)
            (export / 'demo.desktop').write_text('[Desktop Entry]\nName=Demo\n')
            with patch('glyph.paths.in_flatpak', return_value=True), patch.object(
                    Path, 'home', return_value=home), patch.dict(os.environ, {
                        'XDG_DATA_HOME': str(private), 'HOST_XDG_DATA_HOME': str(host),
                        'XDG_DATA_DIRS': '/app/share:/usr/share'}):
                self.assertEqual(user_application_dirs(), [host / 'applications'])
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


if __name__ == "__main__":
    unittest.main()
