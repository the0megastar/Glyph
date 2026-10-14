from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil

from gi.repository import Gio, GLib

import glyph.paths as paths
from glyph.paths import find_stock, stock_dirs


import glyph.sources as sources


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
    package_kind: str = "unknown"
    install_scope: str = "unknown"
    management: str = "external_launcher"
    version: str = ""
    version_provenance: str = ""
    source_evidence: str = ""
    custom_command: bool = False
    original_command: str = ""
    is_appimage: bool = False
    appimage_path: str = ""
    is_theme_icon: bool = False
    flatpak_id: str = ""


@dataclass
class _DesktopEntry:
    desktop_id: str
    filename: str
    name: str
    command: str
    icon: str
    flatpak_id: str
    hidden: bool
    no_display: bool
    only_show_in: tuple[str, ...]
    not_show_in: tuple[str, ...]
    try_exec: str


def get_stock_application_dirs() -> list[Path]:
    return stock_dirs()


def find_stock_desktop_file(desktop_id: str, index: dict[str, Path] | None = None) -> str:
    return find_stock(desktop_id, index=index)


def classify_source(path: str) -> str:
    normalized = path.replace("\\", "/")
    if "/flatpak/" in normalized:
        return "Flatpak"
    if "/snapd/" in normalized or "/snap/" in normalized:
        return "Snap"
    if "/run/host/" in normalized:
        return "System"
    local_roots = (str(root).replace("\\", "/") + "/" for root in paths.user_application_dirs())
    if any(normalized.startswith(root) for root in local_roots):
        return "Local"
    return "System"


def _key_string(key_file: GLib.KeyFile, key: str) -> str:
    try:
        return key_file.get_string("Desktop Entry", key) or ""
    except GLib.Error:
        return ""


def _key_boolean(key_file: GLib.KeyFile, key: str) -> bool:
    try:
        return key_file.get_boolean("Desktop Entry", key)
    except GLib.Error:
        return False


def _key_list(key_file: GLib.KeyFile, key: str) -> tuple[str, ...]:
    try:
        return tuple(value for value in key_file.get_string_list("Desktop Entry", key) if value)
    except GLib.Error:
        return ()


def _load_desktop_entry(desktop_id: str, filename: Path) -> _DesktopEntry | None:
    """Parse launcher metadata without constructing or launching an AppInfo."""
    key_file = GLib.KeyFile()
    try:
        key_file.load_from_file(str(filename), GLib.KeyFileFlags.KEEP_TRANSLATIONS)
        if _key_string(key_file, "Type") != "Application":
            return None
        try:
            name = key_file.get_locale_string("Desktop Entry", "Name", None) or ""
        except GLib.Error:
            name = ""
    except GLib.Error:
        return None

    if not name:
        return None
    return _DesktopEntry(
        desktop_id=desktop_id,
        filename=str(filename),
        name=name,
        command=_key_string(key_file, "Exec"),
        icon=_key_string(key_file, "Icon"),
        flatpak_id=_key_string(key_file, "X-Flatpak"),
        hidden=_key_boolean(key_file, "Hidden"),
        no_display=_key_boolean(key_file, "NoDisplay"),
        only_show_in=_key_list(key_file, "OnlyShowIn"),
        not_show_in=_key_list(key_file, "NotShowIn"),
        try_exec=_key_string(key_file, "TryExec"),
    )


def _try_exec_available(command: str) -> bool:
    if not command:
        return True
    if os.path.isabs(command):
        return os.path.isfile(command) and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def _should_show(entry: _DesktopEntry) -> bool:
    if entry.hidden or entry.no_display:
        return False

    current = {value for value in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if value}
    if entry.only_show_in and not current.intersection(entry.only_show_in):
        return False
    if entry.not_show_in and current.intersection(entry.not_show_in):
        return False

    # The sandbox's PATH and absolute paths do not describe the host. Applying
    # TryExec there would incorrectly reject valid host launchers. Native Glyph
    # still honors the desktop-entry specification's TryExec rule.
    if entry.try_exec and not paths.in_flatpak() and not _try_exec_available(entry.try_exec):
        return False
    return True


def _resolve_app_folder(entry: _DesktopEntry) -> str:
    candidate_id = entry.flatpak_id or entry.desktop_id.removesuffix(".desktop")
    if candidate_id:
        deployments = (
            paths.data_home() / "flatpak/app" / candidate_id / "current/active",
            Path("/var/lib/flatpak/app") / candidate_id / "current/active",
        )
        for deployment in deployments:
            if deployment.is_dir():
                return str(deployment)

    try:
        tokens = shlex.split(entry.command)
    except ValueError:
        tokens = entry.command.split()
    if not tokens:
        return ""

    command = tokens[0]
    if Path(command).name == "gapplication":
        return ""
    if os.path.isabs(command):
        path = Path(command)
        if path.is_file() or path.parent.is_dir():
            return str(path.parent)
        return ""
    resolved = shutil.which(command)
    return str(Path(resolved).parent) if resolved else ""


def _gicon(value: str) -> Gio.Icon | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return Gio.FileIcon.new(Gio.File.new_for_path(value))
    # Match DesktopAppInfo's handling of legacy themed icon filenames.
    if value.endswith(('.png', '.svg', '.xpm')):
        value = value.rsplit('.', 1)[0]
    return Gio.ThemedIcon.new(value)


def list_apps(
    overrides: dict[str, dict],
    *,
    resolve_source_metadata: bool = True,
) -> list[AppEntry]:
    apps: list[AppEntry] = []
    active_index, stock_index, local_index = paths.application_indexes()
    local_files = set(local_index.values())

    system_files = set(str(p) for p in stock_index.values()) | {
        str(p) for p in active_index.values() if p not in local_files
    }
    if resolve_source_metadata:
        sources.prime_system_package_cache(list(system_files))

    for desktop_id, filename_path in active_index.items():
        entry = _load_desktop_entry(desktop_id, filename_path)
        if entry is None or not _should_show(entry):
            continue

        filename = str(filename_path)
        override = overrides.get(desktop_id) or {}
        stock_filename = find_stock_desktop_file(desktop_id, index=stock_index)

        source_info = sources.classify_source_info(
            desktop_id=desktop_id,
            filename=filename,
            command=entry.command,
            stock_filename=stock_filename,
            flatpak_id=entry.flatpak_id,
            override=override,
            resolve_external_metadata=resolve_source_metadata,
        )

        custom_name = bool(override.get("custom_name"))
        custom_command = bool(override.get("custom_command"))
        command = override.get("custom_command") or entry.command

        theme_icon = override.get("theme_icon")
        icon_path = override.get("icon_path")
        gicon = _gicon(entry.icon)
        custom_icon = False
        if theme_icon:
            gicon = Gio.ThemedIcon.new(theme_icon)
            custom_icon = True
        elif icon_path and Path(icon_path).is_file():
            gicon = Gio.FileIcon.new(Gio.File.new_for_path(icon_path))
            custom_icon = True

        appimage_target = override.get("appimage_managed") or sources.is_appimage_target(command)

        apps.append(
            AppEntry(
                desktop_id=desktop_id,
                name=override.get("custom_name") or entry.name or desktop_id,
                filename=filename,
                app_folder=_resolve_app_folder(entry),
                command=command,
                icon_value=entry.icon,
                gicon=gicon,
                source=source_info.display_name,
                custom=custom_icon or custom_name or custom_command,
                has_stock=bool(stock_filename),
                stock_filename=stock_filename,
                custom_icon=custom_icon,
                custom_name=custom_name,
                original_name=override.get("original_name", ""),
                package_kind=source_info.package_kind,
                install_scope=source_info.install_scope,
                management=source_info.management,
                version=source_info.version,
                version_provenance=source_info.version_provenance,
                source_evidence=source_info.source_evidence,
                custom_command=custom_command,
                original_command=override.get("original_command", ""),
                is_appimage=(source_info.package_kind == "appimage"),
                appimage_path=appimage_target,
                is_theme_icon=bool(theme_icon),
                flatpak_id=entry.flatpak_id,
            )
        )

    apps.sort(key=lambda app: app.name.casefold())
    return apps


def resolve_app_sources(apps: list[AppEntry]) -> dict[str, sources.SourceInfo]:
    """Resolve package manager metadata for an already displayed catalog."""
    active_index, stock_index, local_index = paths.application_indexes()
    local_files = set(local_index.values())
    system_files = set(str(p) for p in stock_index.values()) | {
        str(p) for p in active_index.values() if p not in local_files
    }
    sources.prime_system_package_cache(list(system_files))

    result: dict[str, sources.SourceInfo] = {}
    for app in apps:
        managed_appimage = app.management == "glyph_registered"
        override = {
            "glyph_managed_appimage": managed_appimage,
            "appimage_managed": app.appimage_path if managed_appimage else "",
        }
        result[app.desktop_id] = sources.classify_source_info(
            desktop_id=app.desktop_id,
            filename=app.filename,
            command=app.command,
            stock_filename=app.stock_filename,
            flatpak_id=app.flatpak_id,
            override=override,
        )
    return result
