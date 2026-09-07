"""XDG discovery paths and desktop-file IDs, independent of GTK."""
from __future__ import annotations

import os
from pathlib import Path


def data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / ".local/share"


def in_flatpak() -> bool:
    return Path("/.flatpak-info").is_file()


def _xdg_data_roots() -> list[Path]:
    defaults = "/usr/local/share:/usr/share"
    raw = os.environ.get("XDG_DATA_DIRS")
    value = raw if raw else defaults
    return [Path(item) for item in value.split(":") if item and Path(item).is_absolute()]


def shared_data_roots() -> list[Path]:
    """Return host/package data roots in desktop-file precedence order."""
    roots = _xdg_data_roots()
    if in_flatpak():
        # /usr is the Flatpak runtime, not the host OS. Keep /app available to
        # GTK via configure_xdg_data_dirs(), but do not catalog runtime apps.
        roots = [
            Path("/run/host/usr/local/share"),
            Path("/run/host/usr/share"),
            *(root for root in roots if str(root) not in {"/app/share", "/usr/share", "/usr/local/share"}),
        ]
    roots.extend(
        [
            data_home() / "flatpak/exports/share",
            Path("/var/lib/flatpak/exports/share"),
            Path("/var/lib/snapd/desktop"),
        ]
    )
    return list(dict.fromkeys(roots))


def user_application_dirs() -> list[Path]:
    """Return writable/local launcher roots, highest precedence first."""
    roots = [data_home() / "applications"]
    if in_flatpak():
        # The scoped xdg-data/applications grant exposes the host launcher
        # directory even though XDG_DATA_HOME is app-specific.
        roots.append(Path.home() / ".local/share/applications")
    return list(dict.fromkeys(roots))


def stock_dirs() -> list[Path]:
    """Return non-local application roots used for stock lookup."""
    return [root / "applications" for root in shared_data_roots()]


def application_dirs() -> list[Path]:
    """Return every launcher root in desktop-file precedence order."""
    return list(dict.fromkeys([*user_application_dirs(), *stock_dirs()]))


def icon_dirs() -> list[Path]:
    """Return icon search roots matching the launcher discovery sources."""
    roots = [data_home(), *shared_data_roots()]
    result: list[Path] = []
    for root in roots:
        result.extend((root / "icons", root / "pixmaps"))
    return list(dict.fromkeys(result))


def configure_xdg_data_dirs() -> None:
    """Expose explicit package exports to GLib/GTK's icon lookup."""
    roots = _xdg_data_roots()
    roots.extend(shared_data_roots())
    os.environ["XDG_DATA_DIRS"] = ":".join(str(root) for root in dict.fromkeys(roots))


def desktop_index(directories: list[Path]) -> dict[str, Path]:
    """First directory wins; IDs flatten relative paths per the desktop spec.

    A flat and nested filename yielding the same ID in one root is ambiguous in
    the specification. Prefer the flat file deterministically.
    """
    result: dict[str, Path] = {}
    for root in directories:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*.desktop'), key=lambda p: (len(p.relative_to(root).parts), str(p))):
            if path.is_file():
                desktop_id = '-'.join(path.relative_to(root).parts)
                result.setdefault(desktop_id, path)
    return result


def application_indexes() -> tuple[dict[str, Path], dict[str, Path], dict[str, Path]]:
    """Build active, stock, and local indexes, scanning each root once."""
    stock = desktop_index(stock_dirs())
    local = desktop_index(user_application_dirs())
    active = dict(stock)
    active.update(local)
    return active, stock, local


def find_stock(desktop_id: str, index: dict[str, Path] | None = None) -> str:
    if index is not None:
        path = index.get(desktop_id)
    else:
        path = desktop_index(stock_dirs()).get(desktop_id)
    return str(path) if path else ''
