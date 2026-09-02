from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio


@dataclass
class AppEntry:
    desktop_id: str
    name: str
    filename: str
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
                icon_value=_icon_value(info),
                gicon=gicon,
                source=source,
                custom=custom,
            )
        )

    apps.sort(key=lambda app: app.name.casefold())
    return apps
