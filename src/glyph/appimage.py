"""AppImage safe inspection, organization in ~/Applications, and per-user desktop registration."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import time
import unicodedata

from gi.repository import GLib

import glyph.paths as paths
from glyph.overrides import (
    APPLICATIONS_DIR,
    DATA_DIR,
    FLATPAK_LAUNCHER_STORAGE,
    ICONS_DIR,
    MAX_DESKTOP_BYTES,
    OverrideError,
    _atomic,
    _commit,
    _confined,
    _ensure_dirs,
    _id,
    _read,
    load_state,
    operation,
    refresh_desktop_database,
)

USER_APPLICATIONS_DIR = paths.local_applications_dir()
MAX_APPIMAGE_ICON_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_APPIMAGE_DESKTOP_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_APPIMAGE_STDERR_BYTES = 64 * 1024  # 64 KiB
EXTRACTION_TIMEOUT_SECONDS = 4.0


class AppImageError(Exception):
    """Actionable AppImage inspection, planning, or registration failure."""


class ExtractorOutputLimitExceeded(AppImageError):
    """Extraction output exceeded safe memory bounds."""


@dataclass(frozen=True)
class AppImageMetadata:
    name: str
    comment: str
    exec_cmd: str
    icon_name: str
    icon_data: bytes | None
    icon_suffix: str


@dataclass(frozen=True)
class AppImageRegistrationPlan:
    source_file: Path
    managed_file: Path
    is_in_place: bool
    display_name: str
    desktop_id: str
    launcher_path: Path
    icon_name: str
    icon_data: bytes | None
    icon_suffix: str
    managed_collision: bool
    launcher_collision: bool

    @property
    def has_collision(self) -> bool:
        return self.managed_collision or self.launcher_collision


@dataclass(frozen=True)
class AppImageCandidate:
    path: Path
    resolved_path: Path
    display_name: str


def is_regular_appimage(path: Path) -> bool:
    """Validate that path points to a readable regular AppImage without executing it."""
    try:
        target = path.resolve()
        if not target.is_file():
            return False
        size = target.stat().st_size
        if size < 1024:
            return False
        with target.open("rb") as stream:
            header = stream.read(12)
            if not header.startswith(b"\x7fELF"):
                return False
            # Check for AppImage Type 1 or Type 2 magic at bytes 8-10
            is_type2 = len(header) >= 10 and header[8:11] == b"AI\x02"
            is_type1 = len(header) >= 10 and header[8:11] == b"AI\x01"
            if is_type2 or is_type1:
                return True
            # Also accept file named *.AppImage or *.appimage with ELF header
            return target.name.lower().endswith(".appimage") or path.name.lower().endswith(".appimage")
    except (OSError, PermissionError):
        return False


def _sanitize_name_from_filename(filename: str) -> str:
    """Derive a usable clean display name from an AppImage filename."""
    base = filename
    for ext in (".AppImage", ".appimage"):
        if base.endswith(ext):
            base = base[: -len(ext)]
    # Strip common arch / version suffixes
    base = re.sub(r"[-_](?:x86_64|aarch64|i386|arm64|amd64|linux)", "", base, flags=re.IGNORECASE)
    base = re.sub(r"[-_]v?\d+(?:\.\d+)*.*$", "", base)
    cleaned = base.replace("-", " ").replace("_", " ").strip()
    return cleaned if cleaned else filename


def _run_bounded_extraction(
    args: list[str],
    max_stdout: int,
    max_stderr: int = MAX_APPIMAGE_STDERR_BYTES,
    timeout: float = EXTRACTION_TIMEOUT_SECONDS,
) -> tuple[int, bytes, bytes]:
    """Execute extraction command with streaming bounded output and strict timeouts.
    
    If stdout or stderr exceeds its limit, the child process is terminated immediately
    and reaped, raising ExtractorOutputLimitExceeded.
    """
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_buf = bytearray()
    stderr_buf = bytearray()

    if proc.stdout:
        os.set_blocking(proc.stdout.fileno(), False)
    if proc.stderr:
        os.set_blocking(proc.stderr.fileno(), False)

    sel = selectors.DefaultSelector()
    if proc.stdout:
        sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    if proc.stderr:
        sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

    deadline = time.monotonic() + timeout

    try:
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)

            events = sel.select(timeout=min(remaining, 0.2))
            for key, _ in events:
                stream = key.fileobj
                kind = key.data
                try:
                    chunk = stream.read(65536)
                except (BlockingIOError, OSError):
                    continue
                if not chunk:
                    sel.unregister(stream)
                    try:
                        stream.close()
                    except OSError:
                        pass
                    continue
                if kind == "stdout":
                    stdout_buf.extend(chunk)
                    if len(stdout_buf) > max_stdout:
                        raise ExtractorOutputLimitExceeded(
                            f"Extraction output exceeded maximum size of {max_stdout} bytes"
                        )
                elif kind == "stderr":
                    stderr_buf.extend(chunk)
                    if len(stderr_buf) > max_stderr:
                        raise ExtractorOutputLimitExceeded(
                            f"Extraction stderr exceeded maximum size of {max_stderr} bytes"
                        )

            if proc.poll() is not None and not events:
                break

        try:
            retcode = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            retcode = proc.wait()
            raise
        return retcode, bytes(stdout_buf), bytes(stderr_buf)
    except Exception:
        try:
            proc.terminate()
            proc.wait(timeout=0.2)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
                proc.wait(timeout=0.2)
            except OSError:
                pass
        raise
    finally:
        sel.close()
        if proc.stdout and not proc.stdout.closed:
            try:
                proc.stdout.close()
            except OSError:
                pass
        if proc.stderr and not proc.stderr.closed:
            try:
                proc.stderr.close()
            except OSError:
                pass


def inspect_appimage(path: Path) -> AppImageMetadata:
    """Extract safe metadata from an AppImage without executing it."""
    default_name = _sanitize_name_from_filename(path.name)
    seven_z = shutil.which("7z")
    if not seven_z:
        return AppImageMetadata(
            name=default_name,
            comment="",
            exec_cmd=str(path),
            icon_name="",
            icon_data=None,
            icon_suffix="",
        )

    # 1. Extract embedded .desktop entry if present with bounded extraction
    desktop_text = ""
    try:
        retcode, out, _err = _run_bounded_extraction(
            [seven_z, "e", "-so", str(path), "*.desktop"],
            max_stdout=MAX_APPIMAGE_DESKTOP_BYTES,
            timeout=EXTRACTION_TIMEOUT_SECONDS,
        )
        if retcode == 0:
            decoded = out.decode("utf-8", errors="replace")
            if "[Desktop Entry]" in decoded:
                desktop_text = decoded
    except (subprocess.TimeoutExpired, ExtractorOutputLimitExceeded, OSError):
        desktop_text = ""

    extracted_name = default_name
    extracted_comment = ""
    extracted_icon_name = ""
    if desktop_text:
        kf = GLib.KeyFile()
        try:
            kf.load_from_data(desktop_text, len(desktop_text.encode("utf-8")), GLib.KeyFileFlags.NONE)
            try:
                extracted_name = kf.get_string("Desktop Entry", "Name") or default_name
            except GLib.Error:
                pass
            try:
                extracted_comment = kf.get_string("Desktop Entry", "Comment") or ""
            except GLib.Error:
                pass
            try:
                extracted_icon_name = kf.get_string("Desktop Entry", "Icon") or ""
            except GLib.Error:
                pass
        except GLib.Error:
            pass

    # 2. Extract embedded icon (.DirIcon or <icon_name>.png/svg) with bounded extraction
    icon_data = None
    icon_suffix = ""
    icon_targets = []
    if extracted_icon_name:
        icon_targets.extend([f"{extracted_icon_name}.png", f"{extracted_icon_name}.svg"])
    icon_targets.extend([".DirIcon", "*.png", "*.svg"])

    for target in icon_targets:
        try:
            retcode, data, _err = _run_bounded_extraction(
                [seven_z, "e", "-so", str(path), target],
                max_stdout=MAX_APPIMAGE_ICON_BYTES,
                timeout=EXTRACTION_TIMEOUT_SECONDS,
            )
            if retcode == 0 and len(data) > 32 and len(data) <= MAX_APPIMAGE_ICON_BYTES:
                if data.startswith(b"\x89PNG\r\n\x1a\n"):
                    icon_data = data
                    icon_suffix = ".png"
                    break
                elif b"<svg" in data[:2048].lower():
                    icon_data = data
                    icon_suffix = ".svg"
                    break
        except (subprocess.TimeoutExpired, ExtractorOutputLimitExceeded, OSError):
            continue

    return AppImageMetadata(
        name=extracted_name,
        comment=extracted_comment,
        exec_cmd=str(path),
        icon_name=extracted_icon_name,
        icon_data=icon_data,
        icon_suffix=icon_suffix,
    )


def slugify_name(name: str) -> str:
    """Normalize a display name to a clean, canonical ASCII slug.
    
    Uses Unicode NFKD normalization, strips combining marks,
    drops untransliterable code points, replaces runs of non-alphanumeric
    characters with a single hyphen, trims hyphens, and lowercases.
    Returns 'app' if the resulting slug is empty.
    """
    nfkd = unicodedata.normalize("NFKD", name)
    without_combining = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_bytes = without_combining.encode("ascii", "ignore")
    ascii_str = ascii_bytes.decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str).strip("-")
    return slug if slug else "app"


def derive_desktop_id(
    name: str,
    appimage_path: Path | None = None,
    existing_ids: set[str] | None = None,
) -> str:
    """Derive a canonical, valid ASCII desktop application ID from a display name and optional path."""
    slug = slugify_name(name)
    base_id = f"appimage-{slug}.desktop"

    if existing_ids is None:
        _id(base_id)
        return base_id

    if base_id not in existing_ids:
        _id(base_id)
        return base_id

    # Collision resolution
    if appimage_path is not None:
        try:
            canon_path = str(appimage_path.expanduser().resolve())
        except Exception:
            canon_path = str(appimage_path)
        path_hash = hashlib.sha256(canon_path.encode("utf-8")).hexdigest()[:8].lower()
        hashed_id = f"appimage-{slug}-{path_hash}.desktop"
        if hashed_id not in existing_ids:
            _id(hashed_id)
            return hashed_id

        counter = 2
        while True:
            candidate = f"appimage-{slug}-{path_hash}-{counter}.desktop"
            if candidate not in existing_ids:
                _id(candidate)
                return candidate
            counter += 1
    else:
        counter = 2
        while True:
            candidate = f"appimage-{slug}-{counter}.desktop"
            if candidate not in existing_ids:
                _id(candidate)
                return candidate
            counter += 1


def plan_appimage_registration(source_file: Path, custom_display_name: str = "") -> AppImageRegistrationPlan:
    """Inspect and plan registration of an AppImage, detecting collisions without mutating filesystem."""
    source_file = source_file.expanduser().resolve()
    if not is_regular_appimage(source_file):
        raise AppImageError(f"The selected file is not a readable AppImage: {source_file.name}")

    target_apps_dir = USER_APPLICATIONS_DIR
    is_in_place = source_file.parent.resolve() == target_apps_dir.resolve()

    clean_name = re.sub(r"[^\w\.\-\+]", "_", source_file.name)
    managed_file = target_apps_dir / clean_name if not is_in_place else source_file
    managed_collision = not is_in_place and managed_file.exists()

    metadata = inspect_appimage(source_file)
    display_name = custom_display_name.strip() if custom_display_name.strip() else metadata.name

    desktop_id = derive_desktop_id(display_name)
    launcher_path = APPLICATIONS_DIR / desktop_id
    launcher_collision = launcher_path.exists()

    return AppImageRegistrationPlan(
        source_file=source_file,
        managed_file=managed_file,
        is_in_place=is_in_place,
        display_name=display_name,
        desktop_id=desktop_id,
        launcher_path=launcher_path,
        icon_name=metadata.icon_name,
        icon_data=metadata.icon_data,
        icon_suffix=metadata.icon_suffix,
        managed_collision=managed_collision,
        launcher_collision=launcher_collision,
    )


@operation
def register_appimage(
    plan: AppImageRegistrationPlan,
    chosen_name: str = "",
    *,
    custom_icon_path: str = "",
) -> str:
    """Safely register an AppImage into ~/Applications and create a per-user launcher."""
    _ensure_dirs()
    source = plan.source_file
    managed = plan.managed_file

    if not is_regular_appimage(source):
        raise AppImageError(f"Source file is no longer accessible: {source.name}")

    final_name = chosen_name.strip() if chosen_name.strip() else plan.display_name
    if not final_name:
        raise AppImageError("An application display name is required.")

    desktop_id = (
        plan.desktop_id
        if (not chosen_name or chosen_name.strip() == plan.display_name)
        else derive_desktop_id(final_name)
    )
    launcher_path = APPLICATIONS_DIR / desktop_id

    if launcher_path.exists():
        raise AppImageError(f"A launcher named '{desktop_id}' already exists. Please choose a different name.")

    # Validate optional custom icon upfront before touching the binary
    icon_value = "application-x-executable"
    icon_file_to_save: tuple[Path, bytes] | None = None
    icon_hash = ""

    if custom_icon_path:
        custom_icon = Path(custom_icon_path).expanduser()
        if not custom_icon.is_file():
            raise AppImageError(f"The selected custom icon file does not exist: {custom_icon.name}")
        try:
            icon_bytes = _read(custom_icon, MAX_APPIMAGE_ICON_BYTES)
            from glyph.overrides import validate_image
            validate_image(icon_bytes, custom_icon.suffix)
        except Exception as exc:
            raise AppImageError(f"Invalid custom icon: {exc}") from exc
        icon_hash = hashlib.sha256(icon_bytes).hexdigest()
        icon_dest = ICONS_DIR / f"{desktop_id}-{icon_hash[:12]}{custom_icon.suffix.lower()}"
        icon_file_to_save = (icon_dest, icon_bytes)
        icon_value = str(icon_dest)
    elif plan.icon_data and plan.icon_suffix:
        icon_bytes = plan.icon_data
        icon_hash = hashlib.sha256(icon_bytes).hexdigest()
        icon_dest = ICONS_DIR / f"{desktop_id}-{icon_hash[:12]}{plan.icon_suffix}"
        icon_file_to_save = (icon_dest, icon_bytes)
        icon_value = str(icon_dest)
    elif plan.icon_name:
        icon_value = plan.icon_name

    # Validate state and desktop serialization before touching the managed binary
    state = load_state()

    host_exec = paths.host_applications_dir() / managed.name if paths.in_flatpak() else managed
    kf = GLib.KeyFile()
    kf.set_string("Desktop Entry", "Type", "Application")
    clean_display_name = " ".join(final_name.splitlines()).strip()
    kf.set_string("Desktop Entry", "Name", clean_display_name)
    kf.set_string("Desktop Entry", "Exec", f'"{host_exec}" %U')
    kf.set_string("Desktop Entry", "Icon", icon_value)
    kf.set_boolean("Desktop Entry", "Terminal", False)
    kf.set_string("Desktop Entry", "Categories", "Utility;")
    kf.set_string("Desktop Entry", "X-AppImage-Source", str(source))
    kf.set_string("Desktop Entry", "X-Glyph-Managed", "true")
    try:
        desktop_content = kf.to_data()[0].encode("utf-8")
    except Exception as exc:
        raise AppImageError(f"Failed to serialize desktop launcher: {exc}") from exc
    launcher_hash = hashlib.sha256(desktop_content).hexdigest()

    created_managed_file: Path | None = None
    if not plan.is_in_place:
        if managed.exists():
            raise AppImageError(f"A file named '{managed.name}' already exists in {USER_APPLICATIONS_DIR}.")
        try:
            USER_APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
            temp_copy = USER_APPLICATIONS_DIR / f".tmp-{managed.name}-{os.getpid()}-{hashlib.md5(os.urandom(8)).hexdigest()[:8]}"
            shutil.copy2(source, temp_copy)
            os.chmod(temp_copy, 0o755)
            os.replace(temp_copy, managed)
            created_managed_file = managed
        except Exception as exc:
            if "temp_copy" in locals() and temp_copy.exists():
                try:
                    temp_copy.unlink()
                except OSError:
                    pass
            raise AppImageError(f"Failed to copy AppImage into ~/Applications: {exc}") from exc
    else:
        try:
            os.chmod(managed, managed.stat().st_mode | 0o755)
        except OSError as exc:
            raise AppImageError(f"Failed to make AppImage executable: {exc}") from exc

    try:
        record = {
            "created_local": True,
            "glyph_managed_appimage": True,
            "appimage_source": str(source),
            "appimage_managed": str(managed),
            "local_desktop": str(launcher_path),
            "source_path": str(launcher_path),
            "original_name": clean_display_name,
            "original_icon": icon_value,
            "original_command": f'"{host_exec}" %U',
            "original_text": desktop_content.decode("utf-8", errors="replace"),
            "last_hash": launcher_hash,
            "icon_path": str(icon_file_to_save[0]) if icon_file_to_save else "",
            "icon_hash": icon_hash if icon_file_to_save else "",
        }
        if paths.in_flatpak():
            record["launcher_storage"] = FLATPAK_LAUNCHER_STORAGE
        state[desktop_id] = record

        writes: dict[Path, bytes | None] = {launcher_path: desktop_content}
        if icon_file_to_save:
            writes[icon_file_to_save[0]] = icon_file_to_save[1]

        _commit(state, writes)
    except Exception:
        if created_managed_file and created_managed_file.exists():
            try:
                created_managed_file.unlink()
            except OSError:
                pass
        raise

    refresh_desktop_database()
    return desktop_id


@operation
def unregister_appimage(desktop_id: str, *, delete_icon: bool = True) -> dict[str, bool]:
    """Unregister an AppImage removing only unchanged Glyph-created desktop & icon assets."""
    _id(desktop_id)
    state = load_state()
    record = state.get(desktop_id)
    if not record or not record.get("glyph_managed_appimage"):
        raise AppImageError("This launcher was not registered by Glyph.")

    launcher_path = APPLICATIONS_DIR / desktop_id
    _confined(launcher_path, APPLICATIONS_DIR)
    if launcher_path.is_symlink():
        raise AppImageError("Refusing to remove a symbolic link launcher.")
    if not launcher_path.is_file():
        raise AppImageError(f"Launcher file does not exist: {launcher_path.name}")

    # Verify launcher hash independently before deletion. A missing or malformed
    # hash cannot establish that this is still the launcher Glyph created.
    expected_launcher_hash = record.get("last_hash", "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_launcher_hash):
        raise AppImageError(
            f"The desktop launcher '{desktop_id}' has no valid saved integrity hash. "
            "Unregistration was cancelled to preserve your changes."
        )

    current_launcher_bytes = _read(launcher_path, MAX_DESKTOP_BYTES)
    current_launcher_hash = hashlib.sha256(current_launcher_bytes).hexdigest()
    if current_launcher_hash != expected_launcher_hash:
        raise AppImageError(
            f"The desktop launcher '{desktop_id}' has been modified externally since registration. "
            "Unregistration was cancelled to preserve your changes."
        )

    icon_path_to_delete: Path | None = None
    icon_cleanup_skipped = False
    icon_path_str = record.get("icon_path", "")

    if icon_path_str:
        from glyph.overrides import check_managed_icon_deletion
        eligible, asset_path, reason = check_managed_icon_deletion(desktop_id, state)
        if not eligible and reason in ("modified", "missing_hash"):
            icon_cleanup_skipped = True
        elif eligible and delete_icon and asset_path:
            icon_path_to_delete = asset_path

    writes: dict[Path, bytes | None] = {launcher_path: None}
    if icon_path_to_delete:
        writes[icon_path_to_delete] = None

    del state[desktop_id]
    _commit(state, writes)
    refresh_desktop_database()
    return {"icon_skipped": icon_cleanup_skipped}


def discover_registration_candidates(existing_targets: set[str]) -> list[AppImageCandidate]:
    """Discover candidate AppImages in ~/Applications not yet registered."""
    if not USER_APPLICATIONS_DIR.is_dir():
        return []

    candidates: list[AppImageCandidate] = []
    try:
        for entry in sorted(USER_APPLICATIONS_DIR.iterdir()):
            if entry.name.startswith("."):
                continue
            try:
                if is_regular_appimage(entry):
                    resolved = entry.resolve()
                    canon_str = str(resolved)
                    entry_str = str(entry)
                    if canon_str not in existing_targets and entry_str not in existing_targets:
                        candidates.append(
                            AppImageCandidate(
                                path=entry,
                                resolved_path=resolved,
                                display_name=_sanitize_name_from_filename(entry.name),
                            )
                        )
            except (OSError, PermissionError):
                continue
    except OSError:
        pass
    candidates.sort(key=lambda c: (c.display_name.casefold(), str(c.path)))
    return candidates


def discover_unregistered_appimages(existing_app_commands: set[str]) -> list[Path]:
    """Discover AppImages in ~/Applications that do not yet have desktop integration."""
    return [c.path for c in discover_registration_candidates(existing_app_commands)]
