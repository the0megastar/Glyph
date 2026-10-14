"""XDG discovery paths and desktop-file IDs, independent of GTK."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / ".local/share"


def in_flatpak() -> bool:
    return Path("/.flatpak-info").is_file()


def host_data_home() -> Path:
    """The desktop's data root, distinct from Flatpak's private data root."""
    if not in_flatpak():
        return data_home()
    value = os.environ.get("HOST_XDG_DATA_HOME", "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / ".local/share"


def host_user_home() -> Path:
    """The desktop user's host home directory across environments."""
    if not in_flatpak():
        return Path.home()
    value = os.environ.get("HOST_XDG_DATA_HOME", "")
    if value and Path(value).is_absolute():
        parts = Path(value).resolve().parts
        if ".local" in parts:
            idx = parts.index(".local")
            return Path(*parts[:idx])
        return Path(value).parent.parent
    spawn = shutil.which("flatpak-spawn")
    if spawn:
        try:
            res = subprocess.run(
                [spawn, "--host", "printenv", "HOME"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return Path(res.stdout.strip())
        except Exception:
            pass
    return Path.home()


def host_applications_dir() -> Path:
    """The user's ~/Applications directory on the host desktop (for host-side Exec targets)."""
    return host_user_home() / "Applications"


def local_applications_dir() -> Path:
    """The writable ~/Applications directory accessible to the running process."""
    return Path.home() / "Applications"


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
    return [data_home() / "applications"]


def stock_dirs() -> list[Path]:
    """Return non-local application roots used for stock lookup."""
    return [root / "applications" for root in shared_data_roots()]


def application_dirs() -> list[Path]:
    """Return every launcher root in desktop-file precedence order."""
    return list(dict.fromkeys([*user_application_dirs(), *stock_dirs()]))


def icon_dirs() -> list[Path]:
    """Return icon search roots matching the launcher discovery sources."""
    roots = [data_home(), *shared_data_roots()]
    candidates: list[Path] = []
    if in_flatpak():
        # Read-only host user themes: $XDG_DATA_HOME/icons, host ~/.icons, and sandbox ~/.icons
        candidates.extend([
            host_data_home() / "icons",
            host_user_home() / ".icons",
            Path.home() / ".icons",
        ])
    for root in roots:
        candidates.extend((root / "icons", root / "pixmaps"))

    allowed_boundaries: list[Path] = []
    if in_flatpak():
        allowed_boundaries.extend([
            host_data_home(),
            host_user_home() / ".icons",
            Path.home() / ".icons",
        ])
    for root in roots:
        allowed_boundaries.append(root)

    resolved_boundaries: list[Path] = []
    for b in allowed_boundaries:
        try:
            resolved_boundaries.append(b.resolve())
        except (OSError, RuntimeError):
            resolved_boundaries.append(b)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            resolved = candidate

        # If candidate is a symlink, avoid following outside allowed boundaries
        if candidate.is_symlink():
            is_inside = any(
                str(resolved).startswith(str(boundary) + "/") or resolved == boundary
                for boundary in resolved_boundaries
            )
            if not is_inside:
                continue

        if resolved not in seen:
            seen.add(resolved)
            deduped.append(candidate)

    return deduped


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
