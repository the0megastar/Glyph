import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glyph import launcher
from glyph.launcher import LaunchError, parse_exec_argv, launch


class TestLauncher(unittest.TestCase):
    def test_parse_exec_argv(self):
        self.assertEqual(
            parse_exec_argv("gedit --new-window %U"),
            ["gedit", "--new-window"],
        )
        self.assertEqual(
            parse_exec_argv("/usr/bin/my-app -f %f --icon %i"),
            ["/usr/bin/my-app", "-f", "--icon"],
        )
        self.assertEqual(
            parse_exec_argv('"my program" arg1 "arg 2"'),
            ["my program", "arg1", "arg 2"],
        )

    def test_launch_native_with_desktop_app_info(self):
        app = MagicMock(desktop_id="test.desktop", filename="", stock_filename="", command="/bin/true")
        mock_info = MagicMock()
        with patch("glyph.launcher.in_flatpak", return_value=False), patch(
            "glyph.launcher.GioUnix.DesktopAppInfo.new", return_value=mock_info
        ):
            launch(app)
            mock_info.launch.assert_called_once_with([], None)

    def test_launch_native_fallback_argv(self):
        app = MagicMock(desktop_id="missing.desktop", filename="", stock_filename="", command="/bin/echo hello")
        with patch("glyph.launcher.in_flatpak", return_value=False), patch(
            "glyph.launcher.GioUnix.DesktopAppInfo.new", return_value=None
        ), patch("glyph.launcher.subprocess.Popen") as mock_popen:
            launch(app)
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            self.assertEqual(args, ["/bin/echo", "hello"])

    def test_launch_flatpak_with_spawn(self):
        app = MagicMock(desktop_id="test.desktop", filename="", stock_filename="", command="/usr/bin/test")
        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("glyph.launcher.in_flatpak", return_value=True), patch(
            "glyph.launcher.shutil.which", return_value="/usr/bin/flatpak-spawn"
        ), patch("glyph.launcher.subprocess.run", mock_run):
            launch(app)
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd, ["/usr/bin/flatpak-spawn", "--host", "gtk-launch", "test.desktop"])

    def test_launch_flatpak_failure_raises_actionable_error(self):
        app = MagicMock(desktop_id="test.desktop", filename="", stock_filename="", command="/usr/bin/test")
        mock_run = MagicMock(return_value=MagicMock(returncode=1, stderr="No such file", stdout=""))
        with patch("glyph.launcher.in_flatpak", return_value=True), patch(
            "glyph.launcher.shutil.which", return_value="/usr/bin/flatpak-spawn"
        ), patch("glyph.launcher.subprocess.run", mock_run):
            with self.assertRaises(LaunchError) as ctx:
                launch(app)
            self.assertIn("Host launch failed", str(ctx.exception))

    def test_launch_flatpak_invalid_desktop_id_fails_without_spawn(self):
        invalid_ids = [
            "",
            "invalid",
            "../escape.desktop",
            "foo/bar.desktop",
            "-leading-dash.desktop",
            "--option.desktop",
            "with space.desktop",
            "with;semi.desktop",
            "with$var.desktop",
            "a" * 250 + ".desktop",
        ]
        mock_run = MagicMock()
        for invalid_id in invalid_ids:
            with patch("glyph.launcher.in_flatpak", return_value=True), patch(
                "glyph.launcher.shutil.which", return_value="/usr/bin/flatpak-spawn"
            ), patch("glyph.launcher.subprocess.run", mock_run):
                with self.assertRaises(LaunchError):
                    launcher.launch_flatpak(invalid_id)
                mock_run.assert_not_called()

                app = MagicMock(desktop_id=invalid_id, filename="", stock_filename="", command="/usr/bin/test")
                with self.assertRaises(LaunchError):
                    launch(app)
                mock_run.assert_not_called()

    def test_launch_flatpak_timeout_raises_actionable_error(self):
        import subprocess
        app = MagicMock(desktop_id="test.desktop", filename="", stock_filename="", command="/usr/bin/test")
        mock_run = MagicMock(side_effect=subprocess.TimeoutExpired(cmd=["flatpak-spawn"], timeout=5))
        with patch("glyph.launcher.in_flatpak", return_value=True), patch(
            "glyph.launcher.shutil.which", return_value="/usr/bin/flatpak-spawn"
        ), patch("glyph.launcher.subprocess.run", mock_run):
            with self.assertRaises(LaunchError) as ctx:
                launch(app)
            self.assertIn("timed out", str(ctx.exception))

    def test_launch_flatpak_missing_tool_raises_error(self):
        app = MagicMock(desktop_id="test.desktop", filename="", stock_filename="", command="/usr/bin/test")
        with patch("glyph.launcher.in_flatpak", return_value=True), patch(
            "glyph.launcher.shutil.which", return_value=None
        ):
            with self.assertRaises(LaunchError) as ctx:
                launch(app)
            self.assertIn("flatpak-spawn", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

