"""Application launch orchestration and host execution adapters."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from gi.repository import Gio, GioUnix, GLib

from glyph.paths import in_flatpak


class LaunchError(Exception):
    """Actionable launch failure safe to display to the user."""


FIELD_CODE_PATTERN = re.compile(r"%[fFuUdDnNickv%]")


def parse_exec_argv(exec_line: str) -> list[str]:
    """Parse a desktop-entry Exec line into argv, stripping field codes."""
    if not exec_line:
        return []

    # Clean field codes per Desktop Entry Specification
    cleaned = FIELD_CODE_PATTERN.sub("", exec_line).strip()
    try:
        tokens = shlex.split(cleaned)
    except ValueError:
        tokens = cleaned.split()
    return [token for token in tokens if token]


def can_launch_apps() -> bool:
    """Return True if the current environment has a supported launch path."""
    if not in_flatpak():
        return True
    return shutil.which("flatpak-spawn") is not None


def launch_native(desktop_id: str, filename: str = "", stock_filename: str = "", command: str = "") -> None:
    """Launch an application on the native host via GIO or direct execution."""
    info = None
    if desktop_id:
        try:
            info = GioUnix.DesktopAppInfo.new(desktop_id)
        except Exception:
            info = None

    if not info and filename and Path(filename).is_file():
        try:
            info = GioUnix.DesktopAppInfo.new_from_filename(filename)
        except Exception:
            info = None

    if not info and stock_filename and Path(stock_filename).is_file():
        try:
            info = GioUnix.DesktopAppInfo.new_from_filename(stock_filename)
        except Exception:
            info = None

    if info:
        try:
            info.launch([], None)
            return
        except GLib.Error as exc:
            raise LaunchError(f"Failed to launch {desktop_id}: {exc.message}") from exc
        except Exception as exc:
            raise LaunchError(f"Failed to launch {desktop_id}: {exc}") from exc

    # Fallback to direct argv execution if DesktopAppInfo cannot be formed
    argv = parse_exec_argv(command)
    if not argv:
        raise LaunchError("No valid executable command or desktop launcher found.")

    try:
        subprocess.Popen(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise LaunchError(f"Cannot execute '{argv[0]}': {exc.strerror}") from exc


DESKTOP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\.\-]*\.desktop$")


def is_valid_desktop_id(desktop_id: str) -> bool:
    """Validate that desktop_id is a well-formed desktop file identifier per the Desktop Entry spec."""
    if not isinstance(desktop_id, str):
        return False
    if len(desktop_id.encode("utf-8")) > 240:
        return False
    if desktop_id.startswith("-") or desktop_id.startswith("."):
        return False
    return bool(DESKTOP_ID_PATTERN.match(desktop_id))


def launch_flatpak(desktop_id: str) -> None:
    """Launch an application from within Flatpak sandbox using host desktop launcher."""
    if not is_valid_desktop_id(desktop_id):
        raise LaunchError(f"Invalid or missing desktop application ID: {desktop_id!r}")

    spawn_bin = shutil.which("flatpak-spawn")
    if not spawn_bin:
        raise LaunchError("Flatpak host spawning tool (flatpak-spawn) is not available.")

    args = [spawn_bin, "--host", "gtk-launch", desktop_id]

    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip() or f"Exited with code {res.returncode}"
            raise LaunchError(f"Host launch failed: {err}")
    except subprocess.TimeoutExpired as exc:
        raise LaunchError("Host launch timed out waiting for response.") from exc
    except OSError as exc:
        raise LaunchError(f"Failed to communicate with host: {exc}") from exc


def launch(app: object) -> None:
    """Launch the given application entry through the appropriate environment adapter."""
    desktop_id = getattr(app, "desktop_id", "")
    filename = getattr(app, "filename", "")
    stock_filename = getattr(app, "stock_filename", "")
    command = getattr(app, "command", "")

    if not desktop_id and not filename and not command:
        raise LaunchError("Application has no launch information.")

    if in_flatpak():
        if not desktop_id or not is_valid_desktop_id(desktop_id):
            raise LaunchError("Cannot launch application without a valid desktop ID.")
        launch_flatpak(desktop_id)
    else:
        launch_native(desktop_id, filename=filename, stock_filename=stock_filename, command=command)
