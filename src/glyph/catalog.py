from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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


def classify_source(path: str) -> str:
    p = path.replace("\\", "/")
    if "/flatpak/" in p:
        return "Flatpak"
    if "/snapd/" in p or "/snap/" in p:
        return "Snap"
    if "/.local/share/applications/" in p:
        return "User"
    return "RPM"


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
        override = overrides.get(desktop_id)
        source_path = (override or {}).get("source_path") or filename
        source = classify_source(source_path)
        custom = override is not None

        gicon = info.get_icon()
        icon_path = (override or {}).get("icon_path")
        if icon_path and Path(icon_path).is_file():
            gicon = Gio.FileIcon.new(Gio.File.new_for_path(icon_path))

        apps.append(
            AppEntry(
                desktop_id=desktop_id,
                name=info.get_display_name() or info.get_name() or desktop_id,
                filename=filename,
                app_folder=app_folder,
                command=command,
                icon_value=_icon_value(info),
                gicon=gicon,
                source=source,
                custom=custom,
            )
        )

    apps.sort(key=lambda app: app.name.casefold())
    return apps

