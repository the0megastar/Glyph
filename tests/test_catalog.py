import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GioUnix", "2.0")
# Gio is provided dynamically by PyGObject's introspection importer.
from gi.repository import Gio, GioUnix  # pyrefly: ignore[missing-module-attribute]

from glyph import catalog
from glyph import paths


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="glyph-catalog-test-")
        self.root = Path(self.tmp.name)
        self.apps = self.root / "applications"
        self.apps.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def launcher(self, name="demo.desktop", text=None) -> Path:
        p = self.apps / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            text or "[Desktop Entry]\nType=Application\nName=Demo\nExec=/bin/true\nIcon=demo\n",
            encoding="utf-8",
        )
        return p

    def test_catalog_builds_one_index_per_reload(self):
        infos = [
            GioUnix.DesktopAppInfo.new_from_filename(str(self.launcher(f"demo{i}.desktop")))
            for i in range(8)
        ]
        self.assertTrue(all(infos))
        with patch.object(Gio.AppInfo, "get_all", return_value=infos), patch.object(
            paths, "desktop_index", return_value={}
        ) as index:
            apps = catalog.list_apps({})
        self.assertEqual(index.call_count, 1, "Catalog should scan stock directories exactly once per reload")
        self.assertEqual(len(apps), 8)

    def test_classify_source(self):
        self.assertEqual(catalog.classify_source("/run/host/usr/share/applications/app.desktop"), "System")
        self.assertEqual(catalog.classify_source("/var/lib/flatpak/exports/share/applications/app.desktop"), "Flatpak")
        self.assertEqual(catalog.classify_source("/var/lib/snapd/desktop/applications/app.desktop"), "Snap")
        self.assertEqual(catalog.classify_source("/usr/share/applications/app.desktop"), "System")

    def test_list_apps_applies_overrides(self):
        p = self.launcher("custom.desktop", "[Desktop Entry]\nType=Application\nName=Base\nExec=/bin/true\n")
        info = GioUnix.DesktopAppInfo.new_from_filename(str(p))
        overrides = {
            "custom.desktop": {
                "custom_name": "My Custom Name",
            }
        }
        with patch.object(Gio.AppInfo, "get_all", return_value=[info]):
            apps = catalog.list_apps(overrides)
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].name, "My Custom Name")
        self.assertTrue(apps[0].custom)
        self.assertTrue(apps[0].custom_name)


if __name__ == "__main__":
    unittest.main()
