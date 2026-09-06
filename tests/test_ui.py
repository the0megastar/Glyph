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
from glyph.overrides import OverrideError
import glyph.overrides as ov


class TestUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="glyph-ui-test-")
        self.root = Path(self.tmp.name)
        self.state_file = self.root / "overrides.json"
        p = patch.object(ov, "STATE_FILE", self.state_file)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
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
        with patch("glyph.window.in_flatpak", return_value=True), patch("glyph.window.Gtk.Label"):
            window.GlyphWindow._fill_detail(stub, app)
            # In flatpak, launch should be disabled and show informative tooltip
            stub._launch_btn.set_sensitive.assert_called_with(False)
            stub._command_row.set_tooltip_text.assert_called_with(
                "Launching external applications is not supported from within Flatpak sandbox"
            )

    def test_primary_menu_contains_revert_all_names(self):
        menu_model = window._primary_menu_model()
        self.assertIsNotNone(menu_model)
        # Search for app.revert-all-names action in menu sections
        found = False
        for s in range(menu_model.get_n_items()):
            section = menu_model.get_item_link(s, Gio.MENU_LINK_SECTION)
            if section:
                for i in range(section.get_n_items()):
                    action = section.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_ACTION)
                    if action and action.get_string() == "app.revert-all-names":
                        found = True
                        break
        self.assertTrue(found, "app.revert-all-names must be in primary menu")


if __name__ == "__main__":
    unittest.main()
