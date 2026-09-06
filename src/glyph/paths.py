"""XDG paths and desktop-file IDs, independent of GTK and the current desktop."""
from __future__ import annotations

import os
from pathlib import Path


def data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / ".local/share"


def in_flatpak() -> bool:
    return Path("/.flatpak-info").is_file()


def stock_dirs() -> list[Path]:
    defaults = "/usr/local/share:/usr/share"
    roots = [Path(p) for p in os.environ.get("XDG_DATA_DIRS", defaults).split(":") if p and Path(p).is_absolute()]
    if in_flatpak():
        # Runtime launchers are not host applications. Host roots precede exports.
        roots = [Path('/run/host/usr/local/share'), Path('/run/host/usr/share')] + [
            p for p in roots if str(p) not in ('/app/share', '/usr/share', '/usr/local/share')]
    roots += [data_home() / 'flatpak/exports/share', Path('/var/lib/flatpak/exports/share'),
              Path('/var/lib/snapd/desktop')]
    return list(dict.fromkeys(p / 'applications' for p in roots))


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


def find_stock(desktop_id: str) -> str:
    path = desktop_index(stock_dirs()).get(desktop_id)
    return str(path) if path else ''
