import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glyph import icon_themes
from glyph.icon_themes import ThemeSummary, ThemeIcon, list_installed_themes, list_theme_icons, search_theme_icons


class TestIconThemes(unittest.TestCase):
    def setUp(self):
        icon_themes.clear_theme_caches()
        self.tmp = tempfile.TemporaryDirectory(prefix="glyph-icon-test-")
        self.root = Path(self.tmp.name)
        self.icons_dir = self.root / "icons"
        self.icons_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def create_theme(self, theme_id: str, display_name: str, inherits: str = "", icons: list[str] | None = None) -> Path:
        tdir = self.icons_dir / theme_id
        tdir.mkdir(parents=True, exist_ok=True)
        index_text = f"[Icon Theme]\nName={display_name}\nComment=Test theme\n"
        if inherits:
            index_text += f"Inherits={inherits}\n"
        (tdir / "index.theme").write_text(index_text, encoding="utf-8")
        if icons:
            apps_dir = tdir / "scalable" / "apps"
            apps_dir.mkdir(parents=True, exist_ok=True)
            for ic in icons:
                (apps_dir / f"{ic}.svg").write_text("<svg></svg>", encoding="utf-8")
        return tdir

    def test_list_installed_themes(self):
        self.create_theme("ThemeA", "Theme Alpha")
        self.create_theme("ThemeB", "Theme Beta")
        with patch("glyph.paths.icon_dirs", return_value=[self.icons_dir]):
            themes = list_installed_themes()
        self.assertEqual(len(themes), 2)
        names = [t.name for t in themes]
        self.assertIn("Theme Alpha", names)
        self.assertIn("Theme Beta", names)

    def test_list_theme_icons_and_inheritance(self):
        self.create_theme("BaseTheme", "Base Theme", icons=["base-calc", "shared-app"])
        self.create_theme("ChildTheme", "Child Theme", inherits="BaseTheme", icons=["child-app", "shared-app"])

        with patch("glyph.paths.icon_dirs", return_value=[self.icons_dir]):
            icons = list_theme_icons("ChildTheme")
        icon_names = [i.name for i in icons]
        self.assertIn("child-app", icon_names)
        self.assertIn("base-calc", icon_names)
        self.assertIn("shared-app", icon_names)

        # Child theme's variant should have higher precedence for shared-app
        shared_icon = next(i for i in icons if i.name == "shared-app")
        self.assertEqual(shared_icon.theme_id, "ChildTheme")

    def test_search_theme_icons(self):
        self.create_theme("SearchTheme", "Search Theme", icons=["calculator", "calendar", "call-start", "camera"])
        with patch("glyph.paths.icon_dirs", return_value=[self.icons_dir]):
            results = search_theme_icons("SearchTheme", "cal")
        names = [r.name for r in results]
        self.assertIn("calculator", names)
        self.assertIn("calendar", names)
        self.assertIn("call-start", names)
        self.assertNotIn("camera", names)


if __name__ == "__main__":
    unittest.main()
