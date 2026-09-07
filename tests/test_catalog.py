import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gi
gi.require_version("GioUnix", "2.0")
from gi.repository import Gio  # pyrefly: ignore[missing-module-attribute]

from glyph import catalog
from glyph import paths


class TestCatalog(unittest.TestCase):
    def test_icon_filename_compatibility(self):
        for value, expected in [('demo.png', 'demo'), ('demo.svg', 'demo'),
                                ('demo.xpm', 'demo'), ('demo.jpg', 'demo.jpg'),
                                ('demo.PNG', 'demo.PNG'), ('demo', 'demo')]:
            with self.subTest(value=value):
                self.assertEqual(catalog._gicon(value).get_names()[0], expected)
        icon = catalog._gicon('/missing/icon.png')
        self.assertIsInstance(icon, Gio.FileIcon)
        self.assertEqual(icon.get_file().get_path(), '/missing/icon.png')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="glyph-catalog-test-")
        self.root = Path(self.tmp.name)
        self.local = self.root / "local" / "applications"
        self.stock = self.root / "stock" / "applications"
        self.local.mkdir(parents=True)
        self.stock.mkdir(parents=True)
        self.path_patches = (
            patch.object(paths, "user_application_dirs", return_value=[self.local]),
            patch.object(paths, "stock_dirs", return_value=[self.stock]),
        )
        for active_patch in self.path_patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.path_patches):
            active_patch.stop()
        self.tmp.cleanup()

    def launcher(self, name="demo.desktop", text=None, *, root=None) -> Path:
        target_root = root or self.stock
        target = target_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            text or "[Desktop Entry]\nType=Application\nName=Demo\nExec=/bin/true\nIcon=demo\n",
            encoding="utf-8",
        )
        return target

    def test_catalog_scans_each_discovery_root_once(self):
        for index in range(8):
            self.launcher(f"demo{index}.desktop")
        with patch.object(paths, "desktop_index", wraps=paths.desktop_index) as build_index:
            apps = catalog.list_apps({})
        self.assertEqual(build_index.call_count, 2, "Stock and local roots should each be indexed once")
        self.assertEqual(len(apps), 8)

    def test_classify_source(self):
        self.assertEqual(catalog.classify_source("/run/host/usr/share/applications/app.desktop"), "System")
        self.assertEqual(catalog.classify_source("/var/lib/flatpak/exports/share/applications/app.desktop"), "Flatpak")
        self.assertEqual(catalog.classify_source("/var/lib/snapd/desktop/applications/app.desktop"), "Snap")
        self.assertEqual(catalog.classify_source(str(self.local / "app.desktop")), "Local")

    def test_list_apps_applies_overrides(self):
        self.launcher("custom.desktop", "[Desktop Entry]\nType=Application\nName=Base\nExec=/bin/true\n")
        overrides = {"custom.desktop": {"custom_name": "My Custom Name"}}
        apps = catalog.list_apps(overrides)
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].name, "My Custom Name")
        self.assertTrue(apps[0].custom)
        self.assertTrue(apps[0].custom_name)

    def test_unavailable_host_executable_is_discovered_in_flatpak(self):
        self.launcher(
            "host.desktop",
            "[Desktop Entry]\nType=Application\nName=Host Tool\n"
            "Exec=/host/bin/not-visible-in-sandbox\nTryExec=/host/bin/not-visible-in-sandbox\n",
        )
        with patch.object(paths, "in_flatpak", return_value=True), patch.object(
            Gio.AppInfo, "get_all", side_effect=AssertionError("catalog must enumerate files directly")
        ):
            apps = catalog.list_apps({})
        self.assertEqual([app.desktop_id for app in apps], ["host.desktop"])
        self.assertEqual(apps[0].command, "/host/bin/not-visible-in-sandbox")

    def test_native_try_exec_still_filters_missing_program(self):
        self.launcher(
            "missing.desktop",
            "[Desktop Entry]\nType=Application\nName=Missing\nExec=missing-command\nTryExec=missing-command\n",
        )
        with patch.object(paths, "in_flatpak", return_value=False), patch.object(
            catalog.shutil, "which", return_value=None
        ):
            self.assertEqual(catalog.list_apps({}), [])

    def test_user_flatpak_export_is_discovered(self):
        export_apps = self.root / "data" / "flatpak" / "exports" / "share" / "applications"
        self.launcher(
            "org.example.App.desktop",
            "[Desktop Entry]\nType=Application\nName=User Flatpak\n"
            "Exec=/usr/bin/flatpak run org.example.App\nX-Flatpak=org.example.App\n",
            root=export_apps,
        )
        with patch.object(paths, "stock_dirs", return_value=[export_apps]):
            apps = catalog.list_apps({})
        self.assertEqual([app.name for app in apps], ["User Flatpak"])
        self.assertEqual(apps[0].source, "Flatpak")

    def test_local_entry_shadows_stock_but_retains_stock_lookup(self):
        stock = self.launcher(
            "shadow.desktop",
            "[Desktop Entry]\nType=Application\nName=Stock Name\nExec=/bin/true\n",
        )
        local = self.launcher(
            "shadow.desktop",
            "[Desktop Entry]\nType=Application\nName=Local Name\nExec=/bin/true\n",
            root=self.local,
        )
        apps = catalog.list_apps({})
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].name, "Local Name")
        self.assertEqual(apps[0].filename, str(local))
        self.assertEqual(apps[0].stock_filename, str(stock))
        self.assertTrue(apps[0].has_stock)

    def test_hidden_local_entry_masks_stock_entry(self):
        self.launcher("masked.desktop")
        self.launcher(
            "masked.desktop",
            "[Desktop Entry]\nType=Application\nName=Masked\nHidden=true\n",
            root=self.local,
        )
        self.assertEqual(catalog.list_apps({}), [])

    def test_desktop_visibility_rules_are_applied(self):
        self.launcher(
            "gnome.desktop",
            "[Desktop Entry]\nType=Application\nName=GNOME App\nOnlyShowIn=GNOME;\n",
        )
        self.launcher(
            "hidden-on-gnome.desktop",
            "[Desktop Entry]\nType=Application\nName=Other App\nNotShowIn=GNOME;\n",
        )
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}):
            apps = catalog.list_apps({})
        self.assertEqual([app.desktop_id for app in apps], ["gnome.desktop"])


if __name__ == "__main__":
    unittest.main()
