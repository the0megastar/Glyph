from __future__ import annotations

import json
import os
import shutil
import subprocess
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


def _local_path_for(source: Path) -> Path:
    return APPLICATIONS_DIR / source.name


def _icon_dest(desktop_id: str, source_image: Path) -> Path:
    suffix = source_image.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise OverrideError(f"Unsupported image type: {suffix or '(none)'}")
    safe_id = desktop_id.replace("/", "_")
    return ICONS_DIR / f"{safe_id}{suffix}"


def refresh_desktop_database(path: Path | None = None) -> None:
    target = path or APPLICATIONS_DIR
    target.mkdir(parents=True, exist_ok=True)
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
    record = {
        "created_local": created_local,
        "icon_path": str(dest_icon),
        "local_desktop": str(local),
        "original_icon": original_icon,
        "source_path": source_path,
    }
    state[desktop_id] = record
    save_state(state)
    refresh_desktop_database()
    return record


def revert_icon(desktop_id: str) -> None:
    state = load_state()
    record = state.get(desktop_id)
    if not record:
        raise OverrideError("No Glyph override for this app.")

    local = Path(record["local_desktop"])
    created_local = bool(record.get("created_local"))

    if created_local:
        if local.is_file():
            local.unlink()
    elif local.is_file():
        text = local.read_text(encoding="utf-8")
        original = record.get("original_icon", "")
        local.write_text(set_icon_value(text, original), encoding="utf-8")
        os.utime(local, None)

    icon_path = record.get("icon_path")
    if icon_path:
        path = Path(icon_path)
        if path.is_file() and ICONS_DIR in path.resolve().parents:
            path.unlink()

    del state[desktop_id]
    save_state(state)
    refresh_desktop_database()
