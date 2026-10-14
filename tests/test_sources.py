import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glyph import sources
from glyph.sources import SourceInfo, classify_source_info, is_appimage_target


class TestSources(unittest.TestCase):
    def setUp(self):
        sources.clear_source_caches()

    def test_flatpak_classification(self):
        with patch.object(sources, "_get_flatpak_cache", return_value={"org.gnome.Calculator": ("50.0", "system")}):
            info = classify_source_info(
                desktop_id="org.gnome.Calculator.desktop",
                filename="/var/lib/flatpak/exports/share/applications/org.gnome.Calculator.desktop",
                command="/usr/bin/flatpak run org.gnome.Calculator",
                flatpak_id="org.gnome.Calculator",
            )
            self.assertEqual(info.package_kind, "flatpak")
            self.assertEqual(info.install_scope, "system")
            self.assertEqual(info.management, "package_managed")
            self.assertEqual(info.version, "50.0")
            self.assertEqual(info.version_provenance, "Flatpak metadata")
            self.assertEqual(info.display_name, "Flatpak")

    def test_snap_classification(self):
        info = classify_source_info(
            desktop_id="snap-app.desktop",
            filename="/var/lib/snapd/desktop/applications/snap-app.desktop",
            command="/snap/bin/snap-app",
        )
        self.assertEqual(info.package_kind, "snap")
        self.assertEqual(info.install_scope, "system")
        self.assertEqual(info.management, "package_managed")
        self.assertEqual(info.display_name, "Snap")

    def test_appimage_command_classification(self):
        self.assertEqual(
            is_appimage_target("/home/user/Applications/Obsidian-1.5.8.AppImage --no-sandbox"),
            "/home/user/Applications/Obsidian-1.5.8.AppImage",
        )
        self.assertEqual(is_appimage_target("/usr/bin/firefox"), "")

        info = classify_source_info(
            desktop_id="obsidian.desktop",
            filename="/home/user/.local/share/applications/obsidian.desktop",
            command="/home/user/Applications/Obsidian-1.5.8.AppImage",
        )
        self.assertEqual(info.package_kind, "appimage")
        self.assertEqual(info.install_scope, "user")
        self.assertEqual(info.management, "external_launcher")
        self.assertEqual(info.display_name, "AppImage")

    def test_glyph_registered_appimage(self):
        info = classify_source_info(
            desktop_id="custom-appimage.desktop",
            filename="/home/user/.local/share/applications/custom-appimage.desktop",
            command="/home/user/Applications/Custom.AppImage",
            override={"glyph_managed_appimage": True, "appimage_managed": "/home/user/Applications/Custom.AppImage"},
        )
        self.assertEqual(info.package_kind, "appimage")
        self.assertEqual(info.install_scope, "user")
        self.assertEqual(info.management, "glyph_registered")
        self.assertEqual(info.display_name, "AppImage")

    def test_native_package_cache_rpm(self):
        sources._system_pkg_cache["/usr/share/applications/test.desktop"] = ("rpm", "1.2.3-1.fc44", "test-pkg")
        info = classify_source_info(
            desktop_id="test.desktop",
            filename="/usr/share/applications/test.desktop",
            command="/usr/bin/test",
        )
        self.assertEqual(info.package_kind, "rpm")
        self.assertEqual(info.install_scope, "system")
        self.assertEqual(info.version, "1.2.3-1.fc44")
        self.assertEqual(info.version_provenance, "RPM package database")
        self.assertIn("test-pkg", info.source_evidence)
        self.assertEqual(info.display_name, "RPM")

    def test_native_package_cache_pacman_no_aur_claim(self):
        sources._system_pkg_cache["/usr/share/applications/arch.desktop"] = ("pacman", "2.0-1", "arch-pkg")
        info = classify_source_info(
            desktop_id="arch.desktop",
            filename="/usr/share/applications/arch.desktop",
            command="/usr/bin/arch-pkg",
        )
        self.assertEqual(info.package_kind, "pacman")
        self.assertEqual(info.version_provenance, "Pacman package database")
        self.assertNotIn("AUR", info.source_evidence)
        self.assertEqual(info.display_name, "Pacman")

    def test_fallback_system_and_user_app(self):
        sys_info = classify_source_info(
            desktop_id="sys.desktop",
            filename="/usr/share/applications/sys.desktop",
            command="/usr/bin/sys",
        )
        self.assertEqual(sys_info.package_kind, "unknown")
        self.assertEqual(sys_info.install_scope, "system")
        self.assertEqual(sys_info.display_name, "System app")

        with patch("glyph.paths.user_application_dirs", return_value=[Path("/home/user/.local/share/applications")]):
            user_info = classify_source_info(
                desktop_id="user.desktop",
                filename="/home/user/.local/share/applications/user.desktop",
                command="/home/user/bin/user",
            )
            self.assertEqual(user_info.package_kind, "unknown")
            self.assertEqual(user_info.install_scope, "user")
            self.assertEqual(user_info.display_name, "User app")

    def test_flatpak_host_package_query_rpm(self):
        sandbox_path = "/run/host/usr/share/applications/calc.desktop"
        with patch("glyph.paths.in_flatpak", return_value=True), \
             patch("glyph.sources._run_host_command") as mock_host_cmd:
            def host_cmd_side_effect(cmd, **kwargs):
                if cmd == ["rpm", "--version"]:
                    return "RPM version 4.20.0\n"
                if len(cmd) >= 4 and cmd[0] == "rpm" and cmd[1] == "-qf":
                    return "gnome-calculator\t47.0-1.fc41\n"
                return None
            mock_host_cmd.side_effect = host_cmd_side_effect

            sources.prime_system_package_cache([sandbox_path])
            self.assertIn(sandbox_path, sources._system_pkg_cache)
            kind, ver, pkg = sources._system_pkg_cache[sandbox_path]
            self.assertEqual(kind, "rpm")
            self.assertEqual(ver, "47.0-1.fc41")
            self.assertEqual(pkg, "gnome-calculator")

            info = classify_source_info(
                desktop_id="calc.desktop",
                filename=sandbox_path,
                command="/usr/bin/gnome-calculator",
            )
            self.assertEqual(info.package_kind, "rpm")
            self.assertEqual(info.version, "47.0-1.fc41")
            self.assertEqual(info.display_name, "RPM")
            self.assertIn("gnome-calculator", info.source_evidence)

    def test_flatpak_host_package_query_deb(self):
        sandbox_path = "/run/host/usr/share/applications/gedit.desktop"
        with patch("glyph.paths.in_flatpak", return_value=True), \
             patch("glyph.sources._run_host_command") as mock_host_cmd:
            def host_cmd_side_effect(cmd, **kwargs):
                if cmd == ["rpm", "--version"]:
                    return None
                if cmd == ["dpkg-query", "--version"]:
                    return "Debian dpkg-query 1.22.0\n"
                if cmd[:2] == ["dpkg-query", "-S"]:
                    return "gedit: /usr/share/applications/gedit.desktop\n"
                if cmd[:3] == ["dpkg-query", "-W", "-f=${Version}"]:
                    return "46.1-2\n"
                return None
            mock_host_cmd.side_effect = host_cmd_side_effect

            sources.prime_system_package_cache([sandbox_path])
            self.assertIn(sandbox_path, sources._system_pkg_cache)
            kind, ver, pkg = sources._system_pkg_cache[sandbox_path]
            self.assertEqual(kind, "deb")
            self.assertEqual(ver, "46.1-2")
            self.assertEqual(pkg, "gedit")

    def test_flatpak_host_package_query_pacman(self):
        sandbox_path = "/run/host/usr/share/applications/kate.desktop"
        with patch("glyph.paths.in_flatpak", return_value=True), \
             patch("glyph.sources._run_host_command") as mock_host_cmd:
            def host_cmd_side_effect(cmd, **kwargs):
                if cmd == ["rpm", "--version"] or cmd == ["dpkg-query", "--version"]:
                    return None
                if cmd == ["pacman", "-V"]:
                    return "Pacman v6.1.0\n"
                if cmd[:2] == ["pacman", "-Qo"]:
                    return "/usr/share/applications/kate.desktop is owned by kate 24.08.0-1\n"
                return None
            mock_host_cmd.side_effect = host_cmd_side_effect

            sources.prime_system_package_cache([sandbox_path])
            self.assertIn(sandbox_path, sources._system_pkg_cache)
            kind, ver, pkg = sources._system_pkg_cache[sandbox_path]
            self.assertEqual(kind, "pacman")
            self.assertEqual(ver, "24.08.0-1")
            self.assertEqual(pkg, "kate")

    def test_flatpak_host_package_query_denied_or_missing(self):
        sandbox_path = "/run/host/usr/share/applications/unknown.desktop"
        with patch("glyph.paths.in_flatpak", return_value=True), \
             patch("glyph.sources._run_host_command", return_value=None):
            sources.prime_system_package_cache([sandbox_path])
            self.assertNotIn(sandbox_path, sources._system_pkg_cache)

            info = classify_source_info(
                desktop_id="unknown.desktop",
                filename=sandbox_path,
                command="/usr/bin/unknown",
            )
            self.assertEqual(info.package_kind, "unknown")
            self.assertEqual(info.display_name, "System app")

    def test_flatpak_host_command_template_isolation(self):
        # Verify that desktop exec contents or malicious paths cannot affect host arguments
        self.assertIsNone(sources._translate_to_host_desktop_path("/etc/shadow"))
        self.assertIsNone(sources._translate_to_host_desktop_path("/run/host/usr/share/applications/../../../etc/shadow"))
        self.assertIsNone(sources._translate_to_host_desktop_path("$(whoami).desktop"))
        self.assertIsNone(sources._translate_to_host_desktop_path("/run/host/usr/share/applications/test;rm -rf /;.desktop"))

        # Valid host path translates safely
        translated = sources._translate_to_host_desktop_path("/run/host/usr/share/applications/org.gnome.Calculator.desktop")
        self.assertEqual(translated, "/usr/share/applications/org.gnome.Calculator.desktop")

        # Verify allowlist enforcement
        self.assertTrue(sources._is_allowed_host_command(["rpm", "--version"]))
        self.assertTrue(sources._is_allowed_host_command(["dpkg-query", "--version"]))
        self.assertTrue(sources._is_allowed_host_command(["pacman", "-V"]))
        self.assertTrue(sources._is_allowed_host_command(["flatpak", "list", "--app", "--columns=application,version,installation"]))
        self.assertTrue(sources._is_allowed_host_command([
            "rpm", "-qf", "/usr/share/applications/org.gnome.Calculator.desktop",
            "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\n",
        ]))
        self.assertTrue(sources._is_allowed_host_command(["dpkg-query", "-S", "/usr/share/applications/org.gnome.Calculator.desktop"]))
        self.assertTrue(sources._is_allowed_host_command(["dpkg-query", "-W", "-f=${Version}", "gnome-calculator"]))
        self.assertTrue(sources._is_allowed_host_command(["pacman", "-Qo", "/usr/share/applications/org.gnome.Calculator.desktop"]))

        # Disallowed commands and arguments fail-closed
        self.assertFalse(sources._is_allowed_host_command(["cat", "/etc/shadow"]))
        self.assertFalse(sources._is_allowed_host_command(["rpm", "-e", "something"]))
        self.assertFalse(sources._is_allowed_host_command(["pacman", "-S", "something"]))
        self.assertFalse(sources._is_allowed_host_command(["flatpak", "uninstall", "org.gnome.Calculator"]))
        self.assertFalse(sources._is_allowed_host_command(["dpkg-query", "-S", "/etc/shadow"]))
        self.assertFalse(sources._is_allowed_host_command(["dpkg-query", "-W", "-f=${Version}", "pkg;rm -rf /"]))
        self.assertIsNone(sources._run_host_command(["cat", "/etc/shadow"]))

    def test_rpm_query_database_full_path_verification(self):
        # Test full path verification when multiple headers match basename
        target = "/usr/share/applications/test.desktop"
        other = "/opt/other/test.desktop"

        class FakeHeader:
            def __init__(self, name, version, release, dirnames, dirindexes, basenames):
                self.name = name
                self.version = version
                self.release = release
                self.dirnames = dirnames
                self.dirindexes = dirindexes
                self.basenames = basenames
            def __getitem__(self, item):
                return getattr(self, item)
            def __contains__(self, item):
                return hasattr(self, item)

        # Header 1 has test.desktop under /opt/other/
        h1 = FakeHeader("wrong-pkg", "1.0", "1", ["/opt/other/"], [0], ["test.desktop"])
        # Header 2 has test.desktop under /usr/share/applications/
        h2 = FakeHeader("correct-pkg", "2.0", "1", ["/usr/share/applications/"], [0], ["test.desktop"])

        fake_ts = MagicMock()
        fake_ts.dbMatch.return_value = [h1, h2]

        with patch.dict(sys.modules, {"rpm": MagicMock(TransactionSet=lambda: fake_ts, RPMTAG_BASENAMES="basenames", RPMTAG_DIRNAMES="dirnames", RPMTAG_DIRINDEXES="dirindexes")}):
            res = sources._query_rpm_database([target])
            self.assertIn(target, res)
            self.assertEqual(res[target], ("rpm", "2.0-1", "correct-pkg"))

    def test_rpm_query_deterministic_tie_break(self):
        target = "/usr/share/applications/test.desktop"

        class FakeHeader:
            def __init__(self, name, version, release):
                self.name = name
                self.version = version
                self.release = release
                self.dirnames = ["/usr/share/applications/"]
                self.dirindexes = [0]
                self.basenames = ["test.desktop"]
            def __getitem__(self, item):
                return getattr(self, item)
            def __contains__(self, item):
                return hasattr(self, item)

        # Two packages claim the exact same file; sort by (name, version) determines winner
        h_zeta = FakeHeader("zeta-pkg", "1.0", "1")
        h_alpha = FakeHeader("alpha-pkg", "1.0", "1")

        fake_ts = MagicMock()
        fake_ts.dbMatch.return_value = [h_zeta, h_alpha]

        with patch.dict(sys.modules, {"rpm": MagicMock(TransactionSet=lambda: fake_ts, RPMTAG_BASENAMES="basenames", RPMTAG_DIRNAMES="dirnames", RPMTAG_DIRINDEXES="dirindexes")}):
            res = sources._query_rpm_database([target])
            self.assertEqual(res[target], ("rpm", "1.0-1", "alpha-pkg"))

    def test_rpm_query_bindings_unavailable_or_error_falls_back_to_cli(self):
        target = "/usr/share/applications/cli.desktop"
        fake_ts = MagicMock()
        fake_ts.dbMatch.side_effect = RuntimeError("DB corrupted")

        with patch.dict(sys.modules, {"rpm": MagicMock(TransactionSet=lambda: fake_ts, RPMTAG_BASENAMES=1)}), \
             patch("shutil.which", return_value="/usr/bin/rpm"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="cli-pkg\t3.0-1\n")
            res = sources._query_rpm_database([target])
            self.assertIn(target, res)
            self.assertEqual(res[target], ("rpm", "3.0-1", "cli-pkg"))

    def test_rpm_query_bindings_empty_result_falls_back_to_cli(self):
        target = "/usr/share/applications/empty_match.desktop"
        fake_ts = MagicMock()
        fake_ts.dbMatch.return_value = []

        with patch.dict(sys.modules, {"rpm": MagicMock(TransactionSet=lambda: fake_ts, RPMTAG_BASENAMES=1)}), \
             patch("shutil.which", return_value="/usr/bin/rpm"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="fallback-pkg\t4.0-1\n")
            res = sources._query_rpm_database([target])
            self.assertIn(target, res)
            self.assertEqual(res[target], ("rpm", "4.0-1", "fallback-pkg"))

    def test_rpm_query_cli_failure_returns_empty(self):
        target = "/usr/share/applications/unowned.desktop"
        with patch.dict(sys.modules, {"rpm": None}), \
             patch("shutil.which", return_value="/usr/bin/rpm"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            res = sources._query_rpm_database([target])
            self.assertEqual(res, {})


if __name__ == "__main__":
    unittest.main()
