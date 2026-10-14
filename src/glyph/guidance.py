"""Version-aware install and update guidance for application detail views."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from gi.repository import Gio, GioUnix

import glyph.paths as paths


@dataclass(frozen=True)
class UpdateGuidance:
    title: str
    summary: str
    instructions: str
    has_software_tool: bool
    software_tool_name: str
    terminal_command: str = ""


def get_update_guidance(
    *,
    package_kind: str,
    install_scope: str,
    version: str = "",
    name: str = "",
) -> UpdateGuidance:
    """Generate concise, source-aware guidance without running package managers."""
    has_gnome_software = bool(
        shutil.which("gnome-software")
        or GioUnix.DesktopAppInfo.new("org.gnome.Software.desktop")
        or paths.find_stock("org.gnome.Software.desktop")
    )
    tool_name = "Software" if has_gnome_software else ""

    ver_note = f"Current version: {version}." if version else "Version is not recorded in package metadata."

    if package_kind == "rpm":
        return UpdateGuidance(
            title="RPM package",
            summary=f"Managed by your distribution's RPM package manager. {ver_note}",
            instructions="On Fedora and RPM distributions, packages are kept up to date through DNF or the Software app.",
            has_software_tool=has_gnome_software,
            software_tool_name=tool_name,
            terminal_command="sudo dnf upgrade --refresh",
        )

    if package_kind == "flatpak":
        scope_flag = "--user" if install_scope == "user" else "--system"
        return UpdateGuidance(
            title="Flatpak application",
            summary=f"Installed in an isolated Flatpak container ({install_scope} installation). {ver_note}",
            instructions="Flatpak applications can be updated through the Software app or via the command line.",
            has_software_tool=has_gnome_software,
            software_tool_name=tool_name,
            terminal_command=f"flatpak update {scope_flag}",
        )

    if package_kind == "deb":
        return UpdateGuidance(
            title="Debian / Ubuntu package",
            summary=f"Managed through the DPKG and APT package system. {ver_note}",
            instructions="To update package indexes and upgrade installed Debian/Ubuntu packages, run the apt commands.",
            has_software_tool=has_gnome_software,
            software_tool_name=tool_name,
            terminal_command="sudo apt update && sudo apt upgrade",
        )

    if package_kind == "pacman":
        return UpdateGuidance(
            title="Pacman package",
            summary=f"Managed by the Arch Linux Pacman package manager. {ver_note}",
            instructions="To synchronize repositories and update all system packages on Arch, run the pacman command.",
            has_software_tool=False,
            software_tool_name="",
            terminal_command="sudo pacman -Syu",
        )

    if package_kind == "snap":
        return UpdateGuidance(
            title="Snap application",
            summary="Managed through the Snap daemon. Updates are normally applied automatically in the background.",
            instructions="To refresh all installed Snaps manually in the terminal, run the snap refresh command.",
            has_software_tool=False,
            software_tool_name="",
            terminal_command="sudo snap refresh",
        )

    if package_kind == "appimage":
        return UpdateGuidance(
            title="AppImage standalone",
            summary="Portable application binary located in ~/Applications.",
            instructions=(
                "To update this AppImage, download the latest version from its developer and place it in your "
                "~/Applications folder. Glyph will link your existing launcher to the updated file."
            ),
            has_software_tool=False,
            software_tool_name="",
            terminal_command="",
        )

    if install_scope == "user":
        return UpdateGuidance(
            title="User launcher",
            summary="Custom or per-user application launcher created in your personal directory.",
            instructions=(
                "This launcher points to a locally installed program or script. Updates are managed manually "
                "by replacing or upgrading the target executable."
            ),
            has_software_tool=False,
            software_tool_name="",
            terminal_command="",
        )

    if install_scope == "system":
        sys_cmd = ""
        if shutil.which("dnf"):
            sys_cmd = "sudo dnf upgrade --refresh"
        elif shutil.which("apt"):
            sys_cmd = "sudo apt update && sudo apt upgrade"
        elif shutil.which("pacman"):
            sys_cmd = "sudo pacman -Syu"

        return UpdateGuidance(
            title="System application",
            summary=f"Installed system-wide. {ver_note}",
            instructions="System applications are updated through your Linux distribution's software updates.",
            has_software_tool=has_gnome_software,
            software_tool_name=tool_name,
            terminal_command=sys_cmd,
        )

    return UpdateGuidance(
        title="Unknown source",
        summary="This application's package format or installation source could not be verified.",
        instructions=(
            "Version and update status are unknown. Please consult the documentation for this application "
            "to check for available updates."
        ),
        has_software_tool=False,
        software_tool_name="",
        terminal_command="",
    )


def open_software_tool(app_id: str = "") -> bool:
    """Open the system Software tool, optionally focused on a specific app ID."""
    clean_id = app_id.removesuffix(".desktop").strip() if app_id else ""

    # 1. Flatpak environment: spawn on host
    if paths.in_flatpak():
        spawn = shutil.which("flatpak-spawn")
        if spawn:
            try:
                if clean_id:
                    res = subprocess.run(
                        [spawn, "--host", "gnome-software", f"--details={clean_id}"],
                        capture_output=True,
                        timeout=5,
                    )
                    if res.returncode == 0:
                        return True
                    # Fallback to gio open appstream URI on host
                    res_uri = subprocess.run(
                        [spawn, "--host", "gio", "open", f"appstream://{clean_id}"],
                        capture_output=True,
                        timeout=5,
                    )
                    if res_uri.returncode == 0:
                        return True
                else:
                    res = subprocess.run([spawn, "--host", "gnome-software"], capture_output=True, timeout=5)
                    if res.returncode == 0:
                        return True
            except Exception:
                pass

    # 2. Native environment: try direct CLI with --details or DesktopAppInfo / GIO
    if clean_id:
        if shutil.which("gnome-software"):
            try:
                subprocess.Popen(["gnome-software", f"--details={clean_id}"], start_new_session=True)
                return True
            except OSError:
                pass

        info = GioUnix.DesktopAppInfo.new("org.gnome.Software.desktop")
        if info:
            try:
                info.launch_uris([f"appstream://{clean_id}"], None)
                return True
            except Exception:
                pass

        try:
            if Gio.AppInfo.launch_default_for_uri(f"appstream://{clean_id}", None):
                return True
        except Exception:
            pass

    # 3. Fallback: general software overview
    info = GioUnix.DesktopAppInfo.new("org.gnome.Software.desktop")
    if info:
        try:
            info.launch([], None)
            return True
        except Exception:
            pass
    if shutil.which("gnome-software"):
        try:
            subprocess.Popen(["gnome-software"], start_new_session=True)
            return True
        except OSError:
            pass

    return False

