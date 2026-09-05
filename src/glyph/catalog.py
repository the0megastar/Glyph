from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shlex
import shutil

from gi.repository import Gio


@dataclass
class AppEntry:
    desktop_id: str
    name: str
    filename: str
    app_folder: str
    command: str
    icon_value: str
    gicon: Gio.Icon | None
    source: str
    custom: bool
    has_stock: bool = False
    stock_filename: str = ""
    custom_icon: bool = False
    custom_name: bool = False
    original_name: str = ""


XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
USER_APPLICATIONS_DIR = XDG_DATA_HOME / "applications"


def get_stock_application_dirs() -> list[Path]:
    dirs: list[Path] = [
        Path("/run/host/usr/share/applications"),
        Path("/run/host/usr/local/share/applications"),
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/snapd/desktop/applications"),
    ]
    xdg_dirs = os.environ.get("XDG_DATA_DIRS", "")
    for d in xdg_dirs.split(":"):
        d = d.strip()
        if d:
            candidate = Path(d) / "applications"
            if candidate not in dirs and candidate.is_dir():
                dirs.append(candidate)
    return dirs


def find_stock_desktop_file(desktop_id: str) -> str:
    name = desktop_id if desktop_id.endswith(".desktop") else f"{desktop_id}.desktop"
    for directory in get_stock_application_dirs():
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    return ""


def classify_source(path: str) -> str:
    p = path.replace("\\", "/")
    if "/run/host/" in p:
        return "System"
    if "/flatpak/" in p:
        return "Flatpak"
    if "/snapd/" in p or "/snap/" in p:
        return "Snap"
    if str(USER_APPLICATIONS_DIR) in p or "/.local/share/applications/" in p:
        return "Local"
    return "System"



def _icon_value(info: Gio.DesktopAppInfo) -> str:
    try:
        value = info.get_string("Icon")
    except Exception:
        value = None
    return value or ""


def _resolve_app_folder(info: Gio.DesktopAppInfo) -> str:
    # 1. Flatpak: check X-Flatpak key or desktop ID
    flatpak_id = None
    try:
        flatpak_id = info.get_string("X-Flatpak")
    except Exception:
        pass

    if not flatpak_id:
        desktop_id = info.get_id() or ""
        if desktop_id.endswith(".desktop"):
            candidate_id = desktop_id[:-8]
        else:
            candidate_id = desktop_id
    else:
        candidate_id = flatpak_id

    if candidate_id:
        user_deploy = Path.home() / ".local/share/flatpak/app" / candidate_id / "current/active"
        if user_deploy.is_dir():
            return str(user_deploy)
        sys_deploy = Path("/var/lib/flatpak/app") / candidate_id / "current/active"
        if sys_deploy.is_dir():
            return str(sys_deploy)

    # 2. Exec command resolution
    exec_str = ""
    try:
        exec_str = info.get_string("Exec") or ""
    except Exception:
        pass
    if not exec_str:
        exec_str = info.get_commandline() or ""

    if not exec_str:
        return ""

    try:
        tokens = shlex.split(exec_str)
    except Exception:
        tokens = exec_str.split()

    if not tokens:
        return ""

    cmd = tokens[0]

    # gapplication is a generic helper; app folder cannot be determined from it
    if cmd == "gapplication" or Path(cmd).name == "gapplication":
        return ""

    # Absolute path
    if cmd.startswith("/"):
        p = Path(cmd)
        if p.is_file() or p.parent.is_dir():
            return str(p.parent)
        if p.is_dir():
            return str(p)
        return ""

    # Bare command name
    which = shutil.which(cmd)
    if which:
        return str(Path(which).parent)

    return ""


def list_apps(overrides: dict[str, dict]) -> list[AppEntry]:
    seen: set[str] = set()
    apps: list[AppEntry] = []

    for info in Gio.AppInfo.get_all():
        if not isinstance(info, Gio.DesktopAppInfo):
            desktop_id = info.get_id()
            if not desktop_id:
                continue
            loaded = Gio.DesktopAppInfo.new(desktop_id)
            if loaded is None:
                continue
            info = loaded

        if not info.should_show():
            continue

        desktop_id = info.get_id()
        if not desktop_id or desktop_id in seen:
            continue
        seen.add(desktop_id)

        filename = info.get_filename() or ""
        app_folder = _resolve_app_folder(info)
        command = info.get_commandline() or ""
        override = overrides.get(desktop_id) or {}
        custom_icon = bool(override.get("icon_path"))
        custom_name = bool(override.get("custom_name"))
        custom = custom_icon or custom_name
        original_name = override.get("original_name", "")

        stock_filename = find_stock_desktop_file(desktop_id)
        has_stock = bool(stock_filename)
        is_local_file = (str(USER_APPLICATIONS_DIR) in filename) or ("/.local/share/applications/" in filename.replace("\\", "/"))

        if is_local_file and has_stock:
            source = classify_source(stock_filename)
        elif is_local_file:
            source = "Local"
        else:
            source = classify_source(filename)

        gicon = info.get_icon()
        icon_path = override.get("icon_path")
        if icon_path and Path(icon_path).is_file():
            gicon = Gio.FileIcon.new(Gio.File.new_for_path(icon_path))

        display_name = override.get("custom_name") or info.get_display_name() or info.get_name() or desktop_id

        apps.append(
            AppEntry(
                desktop_id=desktop_id,
                name=display_name,
                filename=filename,
                app_folder=app_folder,
                command=command,
                icon_value=_icon_value(info),
                gicon=gicon,
                source=source,
                custom=custom,
                has_stock=has_stock,
                stock_filename=stock_filename,
                custom_icon=custom_icon,
                custom_name=custom_name,
                original_name=original_name,
            )
        )

    apps.sort(key=lambda app: app.name.casefold())
    return apps

