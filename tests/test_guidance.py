import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glyph import guidance


class TestGuidance(unittest.TestCase):
    def test_open_software_tool_native_with_app_id(self):
        with patch("glyph.paths.in_flatpak", return_value=False), \
             patch("glyph.guidance.shutil.which", return_value="/usr/bin/gnome-software"), \
             patch("glyph.guidance.subprocess.Popen") as mock_popen:
            res = guidance.open_software_tool("re.sonny.Workbench.desktop")
            self.assertTrue(res)
            mock_popen.assert_called_once_with(
                ["gnome-software", "--details=re.sonny.Workbench"],
                start_new_session=True,
            )

    def test_open_software_tool_native_general(self):
        with patch("glyph.paths.in_flatpak", return_value=False), \
             patch("glyph.guidance.shutil.which", return_value="/usr/bin/gnome-software"), \
             patch("glyph.guidance.GioUnix.DesktopAppInfo.new", return_value=None), \
             patch("glyph.guidance.subprocess.Popen") as mock_popen:
            res = guidance.open_software_tool()
            self.assertTrue(res)
            mock_popen.assert_called_once_with(
                ["gnome-software"],
                start_new_session=True,
            )

    def test_open_software_tool_flatpak_with_app_id(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        with patch("glyph.paths.in_flatpak", return_value=True), \
             patch("glyph.guidance.shutil.which", return_value="/usr/bin/flatpak-spawn"), \
             patch("glyph.guidance.subprocess.run", return_value=mock_res) as mock_run:
            res = guidance.open_software_tool("re.sonny.Workbench")
            self.assertTrue(res)
            mock_run.assert_called_once_with(
                ["/usr/bin/flatpak-spawn", "--host", "gnome-software", "--details=re.sonny.Workbench"],
                capture_output=True,
                timeout=5,
            )

    def test_open_software_tool_flatpak_fallback_to_gio_open(self):
        res_fail = MagicMock(returncode=1)
        res_ok = MagicMock(returncode=0)
        with patch("glyph.paths.in_flatpak", return_value=True), \
             patch("glyph.guidance.shutil.which", return_value="/usr/bin/flatpak-spawn"), \
             patch("glyph.guidance.subprocess.run", side_effect=[res_fail, res_ok]) as mock_run:
            res = guidance.open_software_tool("re.sonny.Workbench")
            self.assertTrue(res)
            self.assertEqual(mock_run.call_count, 2)
            self.assertEqual(
                mock_run.call_args_list[1].args[0],
                ["/usr/bin/flatpak-spawn", "--host", "gio", "open", "appstream://re.sonny.Workbench"],
            )

    def test_open_software_tool_native_desktop_app_info_fallback(self):
        mock_info = MagicMock()
        mock_info.launch_uris.return_value = True
        with patch("glyph.paths.in_flatpak", return_value=False), \
             patch("glyph.guidance.shutil.which", return_value=None), \
             patch("glyph.guidance.GioUnix.DesktopAppInfo.new", return_value=mock_info):
            res = guidance.open_software_tool("org.gnome.Calculator.desktop")
            self.assertTrue(res)
            mock_info.launch_uris.assert_called_once_with(["appstream://org.gnome.Calculator"], None)


if __name__ == "__main__":
    unittest.main()
