import os
from pathlib import Path
import tempfile
import unittest

from glyph.paths import data_home, desktop_index, find_stock, in_flatpak, stock_dirs


class TestPaths(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
