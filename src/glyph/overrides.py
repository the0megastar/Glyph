from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
APPLICATIONS_DIR = XDG_DATA_HOME / "applications"
DATA_DIR = XDG_DATA_HOME / "glyph"
ICONS_DIR = DATA_DIR / "icons"
STATE_FILE = DATA_DIR / "overrides.json"

ALLOWED_SUFFIXES = {".png", ".svg", ".jpg", ".jpeg", ".webp"}


class OverrideError(Exception):
    pass


def _ensure_dirs() -> None:
    APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict[str, dict]:
    if not STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_state(state: dict[str, dict]) -> None:
    _ensure_dirs()
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_FILE)


def get_icon_value(text: str) -> str:
    in_entry = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_entry = stripped == "[Desktop Entry]"
            continue
        if in_entry and stripped.startswith("Icon="):
            return stripped.split("=", 1)[1].strip()
    return ""


def set_icon_value(text: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    in_entry = False
    found = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_entry = stripped == "[Desktop Entry]"
            out.append(line if line.endswith("\n") else line + "\n")
            continue
        if in_entry and stripped.startswith("Icon=") and not found:
            out.append(f"Icon={value}\n")
            found = True
            continue
        out.append(line if line.endswith("\n") else line + "\n")

    if not found:
        inserted = False
        rebuilt: list[str] = []
        for line in out:
            rebuilt.append(line)
            if not inserted and line.strip() == "[Desktop Entry]":
                rebuilt.append(f"Icon={value}\n")
                inserted = True
        out = rebuilt
        if not inserted:
            out.insert(0, "[Desktop Entry]\n")
            out.insert(1, f"Icon={value}\n")
    return "".join(out)


def get_name_value(text: str) -> str:
    in_entry = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_entry = stripped == "[Desktop Entry]"
            continue
        if in_entry and stripped.startswith("Name="):
            return stripped.split("=", 1)[1].strip()
    return ""


def set_name_value(text: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    in_entry = False
    found = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_entry = stripped == "[Desktop Entry]"
            out.append(line if line.endswith("\n") else line + "\n")
            continue
        if in_entry and stripped.startswith("Name=") and not found:
            out.append(f"Name={value}\n")
            found = True
            continue
        # Strip localized names so custom name is always used
        if in_entry and stripped.startswith("Name["):
            continue
        out.append(line if line.endswith("\n") else line + "\n")

    if not found:
        inserted = False
        rebuilt: list[str] = []
        for line in out:
            rebuilt.append(line)
            if not inserted and line.strip() == "[Desktop Entry]":
                rebuilt.append(f"Name={value}\n")
                inserted = True
        out = rebuilt
        if not inserted:
            out.insert(0, "[Desktop Entry]\n")
            out.insert(1, f"Name={value}\n")
    return "".join(out)


def _local_path_for(source: Path) -> Path:
    return APPLICATIONS_DIR / source.name


def _icon_dest(desktop_id: str, source_image: Path) -> Path:
    suffix = source_image.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise OverrideError(f"Unsupported image type: {suffix or '(none)'}")
    digest = hashlib.sha256(source_image.read_bytes()).hexdigest()[:12]
    safe_id = desktop_id.replace("/", "_")
    return ICONS_DIR / f"{safe_id}-{digest}{suffix}"


def _unlink_glyph_icon(path: Path | None) -> None:
    if path is None:
        return
    try:
        resolved = path.resolve()
    except OSError:
        return
    if resolved.is_file() and ICONS_DIR in resolved.parents:
        resolved.unlink()


def refresh_desktop_database(path: Path | None = None) -> None:
    target = path or APPLICATIONS_DIR
    target.mkdir(parents=True, exist_ok=True)
    if Path("/.flatpak-info").exists():
        try:
            subprocess.run(
                ["flatpak-spawn", "--host", "update-desktop-database", str(target)],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass
    try:
        subprocess.run(
            ["update-desktop-database", str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pass
    try:
        os.utime(target, None)
    except OSError:
        pass


def apply_icon(desktop_id: str, source_desktop: str, image_path: str) -> dict:
    if not source_desktop:
        raise OverrideError("This app has no desktop file to override.")

    source = Path(source_desktop)
    if not source.is_file():
        raise OverrideError(f"Desktop file not found: {source}")

    image = Path(image_path).expanduser().resolve()
    if not image.is_file():
        raise OverrideError("Selected image does not exist.")

    _ensure_dirs()
    local = _local_path_for(source)
    created_local = not local.exists()

    if created_local:
        shutil.copy2(source, local)
        text = local.read_text(encoding="utf-8")
        original_icon = get_icon_value(text)
        source_path = str(source)
    else:
        text = local.read_text(encoding="utf-8")
        state = load_state()
        existing = state.get(desktop_id)
        original_icon = (existing or {}).get("original_icon", get_icon_value(text))
        source_path = (existing or {}).get("source_path", str(source))
        created_local = bool((existing or {}).get("created_local", False))

    dest_icon = _icon_dest(desktop_id, image)
    shutil.copy2(image, dest_icon)

    local.write_text(set_icon_value(text, str(dest_icon)), encoding="utf-8")
    os.utime(local, None)

    state = load_state()
    record = state.get(desktop_id, {})
    old_icon = record.get("icon_path")
    if old_icon and old_icon != str(dest_icon):
        _unlink_glyph_icon(Path(old_icon))

    record.update({
        "created_local": created_local,
        "icon_path": str(dest_icon),
        "local_desktop": str(local),
        "original_icon": original_icon,
        "source_path": source_path,
    })
    state[desktop_id] = record
    save_state(state)
    refresh_desktop_database()
    return record


def revert_icon(desktop_id: str) -> None:
    state = load_state()
    record = state.get(desktop_id)
    if not record or not record.get("icon_path"):
        raise OverrideError("No custom icon override for this app.")

    local = Path(record["local_desktop"])
    created_local = bool(record.get("created_local"))
    has_custom_name = bool(record.get("custom_name"))

    if not has_custom_name and created_local:
        if local.is_file():
            local.unlink()
        if record.get("icon_path"):
            _unlink_glyph_icon(Path(record["icon_path"]))
        del state[desktop_id]
    else:
        if local.is_file():
            text = local.read_text(encoding="utf-8")
            original = record.get("original_icon", "")
            local.write_text(set_icon_value(text, original), encoding="utf-8")
            os.utime(local, None)
        if record.get("icon_path"):
            _unlink_glyph_icon(Path(record["icon_path"]))
        record.pop("icon_path", None)
        record.pop("original_icon", None)
        if not has_custom_name:
            del state[desktop_id]
        else:
            state[desktop_id] = record

    save_state(state)
    refresh_desktop_database()


def apply_name(desktop_id: str, source_desktop: str, new_name: str) -> dict:
    new_name = new_name.strip()
    if not new_name:
        raise OverrideError("App name cannot be empty.")

    if not source_desktop:
        raise OverrideError("This app has no desktop file to override.")

    source = Path(source_desktop)
    if not source.is_file():
        raise OverrideError(f"Desktop file not found: {source}")

    _ensure_dirs()
    local = _local_path_for(source)
    created_local = not local.exists()

    state = load_state()
    record = state.get(desktop_id, {})

    if created_local:
        shutil.copy2(source, local)
        text = local.read_text(encoding="utf-8")
        original_name = get_name_value(text)
        source_path = str(source)
    else:
        text = local.read_text(encoding="utf-8")
        original_name = record.get("original_name")
        if not original_name:
            if source.is_file():
                original_name = get_name_value(source.read_text(encoding="utf-8"))
            else:
                original_name = get_name_value(text)
        source_path = record.get("source_path", str(source))
        created_local = bool(record.get("created_local", False))

    local.write_text(set_name_value(text, new_name), encoding="utf-8")
    os.utime(local, None)

    record.update({
        "created_local": created_local,
        "custom_name": new_name,
        "local_desktop": str(local),
        "original_name": original_name,
        "source_path": source_path,
    })
    state[desktop_id] = record
    save_state(state)
    refresh_desktop_database()
    return record


def revert_name(desktop_id: str) -> None:
    state = load_state()
    record = state.get(desktop_id)
    if not record or not record.get("custom_name"):
        raise OverrideError("No custom name override for this app.")

    local = Path(record["local_desktop"])
    created_local = bool(record.get("created_local"))
    has_custom_icon = bool(record.get("icon_path"))

    if not has_custom_icon and created_local:
        if local.is_file():
            local.unlink()
        del state[desktop_id]
    else:
        if local.is_file():
            text = local.read_text(encoding="utf-8")
            original = record.get("original_name", "")
            local.write_text(set_name_value(text, original), encoding="utf-8")
            os.utime(local, None)
        record.pop("custom_name", None)
        record.pop("original_name", None)
        if not has_custom_icon:
            del state[desktop_id]
        else:
            state[desktop_id] = record

    save_state(state)
    refresh_desktop_database()


def restore_stock_launcher(desktop_id: str) -> None:
    local_name = desktop_id if desktop_id.endswith(".desktop") else f"{desktop_id}.desktop"
    local_file = APPLICATIONS_DIR / local_name
    if local_file.is_file():
        local_file.unlink()

    state = load_state()
    if desktop_id in state:
        record = state[desktop_id]
        if record.get("icon_path"):
            _unlink_glyph_icon(Path(record["icon_path"]))
        del state[desktop_id]
        save_state(state)

    refresh_desktop_database()


def revert_all_icons() -> int:
    state = load_state()
    count = 0
    for desktop_id in list(state.keys()):
        try:
            restore_stock_launcher(desktop_id)
            count += 1
        except Exception:
            pass
    return count


def restore_all_to_stock() -> int:
    from glyph.catalog import find_stock_desktop_file

    _ensure_dirs()
    count = 0

    # Remove all local .desktop files in ~/.local/share/applications/ that shadow a system package
    if APPLICATIONS_DIR.is_dir():
        for item in list(APPLICATIONS_DIR.glob("*.desktop")):
            if not item.is_file():
                continue
            stock = find_stock_desktop_file(item.name)
            if stock:
                try:
                    if Path(stock).resolve() != item.resolve():
                        item.unlink()
                        count += 1
                except OSError:
                    pass

    # Clean up all Glyph icons and clear state
    state = load_state()
    for record in state.values():
        icon_path = record.get("icon_path")
        if icon_path:
            _unlink_glyph_icon(Path(icon_path))

    save_state({})
    refresh_desktop_database()
    return count


def export_backup(target_path: Path) -> int:
    state = load_state()
    count = len(state)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target_path, "w:gz") as tar:
        if STATE_FILE.is_file():
            tar.add(STATE_FILE, arcname="overrides.json")
        if ICONS_DIR.is_dir():
            tar.add(ICONS_DIR, arcname="icons")
    return count


def import_backup(source_path: Path) -> int:
    if not source_path.is_file():
        raise OverrideError("Backup file does not exist.")

    _ensure_dirs()
    try:
        with tarfile.open(source_path, "r:gz") as tar:
            tar.extractall(DATA_DIR, filter="data")
    except Exception as exc:
        raise OverrideError(f"Failed to extract backup: {exc}")

    state = load_state()
    count = 0
    for _desktop_id, record in state.items():
        local_path = record.get("local_desktop")
        icon_path = record.get("icon_path")
        if local_path and icon_path and Path(icon_path).is_file():
            local = Path(local_path)
            if local.is_file():
                text = local.read_text(encoding="utf-8")
                local.write_text(set_icon_value(text, icon_path), encoding="utf-8")
                os.utime(local, None)
                count += 1
            else:
                source_path = record.get("source_path")
                if source_path and Path(source_path).is_file():
                    shutil.copy2(source_path, local)
                    text = local.read_text(encoding="utf-8")
                    local.write_text(set_icon_value(text, icon_path), encoding="utf-8")
                    os.utime(local, None)
                    count += 1

    refresh_desktop_database()
    return count

