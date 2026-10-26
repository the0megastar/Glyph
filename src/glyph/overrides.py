"""User launcher overrides with independent undo and recoverable file transactions.

No desktop command is executed here. Backups contain preferences and images,
never executable launcher content or trusted destination paths.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
from functools import wraps
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

from glyph.backups import (
    BackupPlan,
    _backup_members,
    export_backup,
    import_backup,
    preview_backup,
)
import re
import stat
import subprocess
import tarfile
import tempfile
import threading

from glyph.desktop_text import (
    _escape,
    _replace_lines,
    _snapshot_lines,
    get_exec_value,
    get_icon_value,
    get_name_value,
    set_exec_value,
    set_icon_value,
    set_name_value,
)
from glyph.paths import data_home, desktop_index, find_stock, in_flatpak

XDG_DATA_HOME = data_home()
APPLICATIONS_DIR = data_home() / 'applications'
FLATPAK_LAUNCHER_STORAGE = 'host-xdg-applications-v1'
DATA_DIR = XDG_DATA_HOME / 'glyph'
ICONS_DIR = DATA_DIR / 'icons'
STATE_FILE = DATA_DIR / 'overrides.json'
ALLOWED_SUFFIXES = {'.png', '.svg', '.jpg', '.jpeg', '.webp'}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BACKUP_BYTES = 100 * 1024 * 1024
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_DESKTOP_BYTES = 1024 * 1024
MAX_ENTRIES = 2000
_lock = threading.RLock()
_local = threading.local()


class OverrideError(Exception):
    """An actionable error safe to display to the user."""


class LauncherModifiedExternallyError(OverrideError):
    """Raised when a desktop file has been modified externally since Glyph recorded its state."""

    def __init__(self, message: str, desktop_id: str):
        super().__init__(message)
        self.desktop_id = desktop_id


@dataclass
class BatchResult:
    completed: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def _ensure_dirs() -> None:
    APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)


def _id(value: str) -> str:
    if (not isinstance(value, str) or not value.endswith('.desktop') or value.startswith('.')
            or '/' in value or '\\' in value or any(ord(c) < 32 for c in value)
            or len(value.encode()) > 240):
        raise OverrideError('Invalid desktop application ID.')
    return value


def _confined(path: Path, root: Path) -> Path:
    # Reject symlinks in the managed relative path, including the leaf. A Flatpak
    # bind mount is not a symlink and remains supported.
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise OverrideError(f'Path is outside the managed directory: {path}') from None
    if not relative.parts or '..' in relative.parts:
        raise OverrideError('Invalid managed path.')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise OverrideError(f'Refusing to modify a symbolic link: {current}')
    if not path.resolve().is_relative_to(root.resolve()):
        raise OverrideError('Path escapes the managed directory.')
    return path


def _read(path: Path, limit: int) -> bytes:
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise OverrideError(f'File exceeds the supported size: {path.name}')
    return data


def _atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.glyph-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _journal_path() -> Path:
    # Old Flatpak journals refer to private launchers, not host launchers.
    return DATA_DIR / ('host-transaction.json' if in_flatpak() else 'transaction.json')


def _target(kind: str, relative: str) -> Path:
    roots = {'launcher': APPLICATIONS_DIR, 'icon': ICONS_DIR, 'state': DATA_DIR}
    if kind not in roots or not isinstance(relative, str):
        raise OverrideError('Invalid transaction target.')
    if kind == 'state' and relative != STATE_FILE.name:
        raise OverrideError('Invalid transaction state target.')
    if kind == 'launcher' and not relative.endswith('.desktop'):
        raise OverrideError('Invalid transaction launcher target.')
    return _confined(roots[kind] / relative, roots[kind])


def _digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _restore_journal() -> None:
    if in_flatpak() and (DATA_DIR / 'transaction.json').exists():
        raise OverrideError(
            'An interrupted operation from an older Flatpak build needs recovery. '
            'Use that build to recover and export your preferences before continuing. '
            f'Previous data has been preserved in {DATA_DIR}.')
    journal = _journal_path()
    if not journal.exists():
        return
    try:
        records = json.loads(_read(journal, MAX_BACKUP_BYTES * 3))
        if not isinstance(records, list) or len(records) > MAX_ENTRIES * 3 + 1:
            raise ValueError('Invalid transaction records')
        targets = []
        for record in records:
            path = _target(record['kind'], record['relative'])
            before = base64.b64decode(record['before'], validate=True) if record['before'] is not None else None
            current = _read(path, MAX_BACKUP_BYTES) if path.exists() else None
            if _digest(current) not in (_digest(before), record['after']):
                raise OverrideError(f'Interrupted operation conflicts with a newer edit to {path}. '
                                    f'Keep {journal.name} and resolve this conflict before editing.')
            targets.append((path, before, int(record['mode']) & 0o777))
        # Validate everything before restoring anything. Recovery is idempotent.
        for path, before, mode in reversed(targets):
            if before is None:
                path.unlink(missing_ok=True)
                _sync_dir(path.parent)
            else:
                _atomic(path, before, mode)
        journal.unlink()
        _sync_dir(DATA_DIR)
    except (ValueError, KeyError, TypeError) as exc:
        raise OverrideError(f'Cannot recover {journal.name}: {exc}. Keep this file for recovery.') from exc


@contextmanager
def _session():
    with _lock:
        if getattr(_local, 'active', False):
            yield
            return
        _ensure_dirs()
        lock_path = _confined(DATA_DIR / 'operation.lock', DATA_DIR)
        with lock_path.open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            _local.active = True
            try:
                _restore_journal()
                yield
            finally:
                _local.active = False


def operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            with _session():
                return function(*args, **kwargs)
        except (OSError, UnicodeError, ValueError, tarfile.TarError) as exc:
            raise OverrideError(str(exc)) from exc
    return wrapped


def _validate_state(state) -> dict[str, dict]:
    if not isinstance(state, dict) or len(state) > MAX_ENTRIES:
        raise OverrideError('Invalid override state. Keep overrides.json for recovery.')
    for desktop_id, record in state.items():
        _id(desktop_id)
        if not isinstance(record, dict):
            raise OverrideError('Invalid override record. Keep overrides.json for recovery.')
        for key in ('local_desktop', 'source_path', 'original_icon', 'original_name', 'custom_name',
                    'icon_path', 'original_text', 'last_hash', 'theme_icon', 'original_command',
                    'custom_command', 'appimage_source', 'appimage_managed'):
            if key in record and not isinstance(record[key], str):
                raise OverrideError(f'Invalid {key} in override state.')
        for bool_key in ('created_local', 'glyph_managed_appimage'):
            if bool_key in record and type(record[bool_key]) is not bool:
                raise OverrideError(f'Invalid {bool_key} in override state.')
        storage = record.get('launcher_storage', '')
        if storage and storage != FLATPAK_LAUNCHER_STORAGE:
            raise OverrideError('Invalid launcher storage marker in override state.')
        recorded_local = Path(record.get('local_desktop', ''))
        if in_flatpak():
            if recorded_local.is_relative_to(APPLICATIONS_DIR):
                # Flatpak's xdg-data/applications mount has the same sandbox path
                # that older builds used for private launchers. Paths alone cannot
                # distinguish those files, so only accept state written with the
                # explicit host-storage marker introduced with this layout.
                if storage != FLATPAK_LAUNCHER_STORAGE:
                    raise OverrideError(
                        'This Flatpak has launcher preferences from an older or '
                        'unmarked build. They cannot be safely matched to host '
                        'launchers. Export preferences with the previous build, '
                        'then move the old overrides.json aside and import the backup. '
                        f'Previous data has been preserved in {DATA_DIR}.')
            else:
                if recorded_local.is_relative_to(XDG_DATA_HOME / 'applications'):
                    raise OverrideError(
                        'Preferences from an older Flatpak build refer to private launchers. '
                        'Export them using that build, then move the old overrides.json aside '
                        'and import the backup here. '
                        f'Previous data has been preserved in {DATA_DIR}.')
                candidate = APPLICATIONS_DIR / recorded_local.name
                if candidate.is_relative_to(APPLICATIONS_DIR) and recorded_local.name == desktop_id:
                    recorded_local = candidate
                    record['local_desktop'] = str(recorded_local)
                    record['launcher_storage'] = FLATPAK_LAUNCHER_STORAGE
                    if record.get('source_path') and Path(record['source_path']).name == desktop_id:
                        record['source_path'] = str(recorded_local)
        local = _confined(recorded_local, APPLICATIONS_DIR)
        if '-'.join(local.relative_to(APPLICATIONS_DIR).parts) != desktop_id:
            raise OverrideError(f'Launcher path does not match {desktop_id}. Keep overrides.json for recovery.')
        if record.get('icon_path'):
            _confined(Path(record['icon_path']), ICONS_DIR)
        for key in ('Name', 'Icon', 'Exec'):
            saved_key = 'exec' if key == 'Exec' else key.lower()
            saved = record.get('original_' + saved_key + '_lines')
            if saved is not None and (not isinstance(saved, list) or any(
                    not isinstance(line, str) or '\n' in line.rstrip('\n') or
                    not re.fullmatch(key + r'(?:\[[^\]\r\n]+\])?\s*=[^\r\n]*\n?', line.strip('\n'))
                    for line in saved)):
                raise OverrideError('Invalid saved desktop keys.')
    return state


@operation
def load_state() -> dict[str, dict]:
    _confined(STATE_FILE, DATA_DIR)
    if not STATE_FILE.exists():
        return {}
    try:
        return _validate_state(json.loads(_read(STATE_FILE, MAX_STATE_BYTES)))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise OverrideError('Cannot read overrides.json. It has been preserved; restore a known-good copy before editing.') from exc


def _commit(state: dict[str, dict], writes: dict[Path, bytes | None]) -> None:
    _validate_state(state)
    state_bytes = (json.dumps(state, indent=2, sort_keys=True) + '\n').encode()
    if len(state_bytes) > MAX_STATE_BYTES:
        raise OverrideError('Override state is too large.')
    writes = dict(writes)
    writes[STATE_FILE] = state_bytes
    if len(writes) > MAX_ENTRIES * 3 + 1:
        raise OverrideError('Too many files in a single transaction.')
    records = []
    for path, after in writes.items():
        if path == STATE_FILE:
            kind, root = 'state', DATA_DIR
        elif path.is_relative_to(ICONS_DIR):
            kind, root = 'icon', ICONS_DIR
        else:
            kind, root = 'launcher', APPLICATIONS_DIR
        _confined(path, root)
        rel = str(path.relative_to(root))
        if len(path.name.encode('utf-8')) > 255:
            raise OverrideError(f'Destination filename exceeds maximum length: {path.name}')
        # Ensure _target can parse and validate this record identically to _restore_journal
        _target(kind, rel)

        if not path.parent.is_dir():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise OverrideError(f'Cannot create directory for {path}: {exc}') from exc
        if not os.access(path.parent, os.W_OK | os.X_OK):
            raise OverrideError(f'Destination directory is not writable: {path.parent}')
        if path.exists() and not os.access(path, os.W_OK):
            raise OverrideError(f'Destination file is not writable: {path}')

        before = _read(path, MAX_BACKUP_BYTES) if path.exists() else None
        records.append({'kind': kind, 'relative': rel,
                        'before': base64.b64encode(before).decode() if before is not None else None,
                        'after': _digest(after),
                        'mode': stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644})
    _atomic(_confined(_journal_path(), DATA_DIR), json.dumps(records).encode(), 0o600)
    try:
        for (path, after), record in zip(writes.items(), records):
            if after is None:
                path.unlink(missing_ok=True)
                _sync_dir(path.parent)
            else:
                _atomic(path, after, record['mode'])
        _journal_path().unlink()
        _sync_dir(DATA_DIR)
    except Exception as exc:
        try:
            _restore_journal()
        except Exception as recovery:
            raise OverrideError(f'Operation failed ({exc}); recovery needs attention: {recovery}') from exc
        raise OverrideError(f'Operation failed; previous files were restored: {exc}') from exc


@operation
def save_state(state: dict[str, dict]) -> None:
    _commit(state, {})


def refresh_desktop_database(path: Path | None = None) -> None:
    target = path or APPLICATIONS_DIR
    try:
        subprocess.run(['update-desktop-database', str(target)], check=False,
                       capture_output=True, timeout=5)
        os.utime(target, None)
    except (OSError, subprocess.TimeoutExpired):
        pass  # Cache refresh is best effort after a successful transaction.


def _local_path_for(source: Path, desktop_id: str) -> Path:
    _id(desktop_id)
    if source.is_relative_to(APPLICATIONS_DIR):
        local = source
    else:
        local = desktop_index([APPLICATIONS_DIR]).get(desktop_id, APPLICATIONS_DIR / desktop_id)
    _confined(local, APPLICATIONS_DIR)
    if '-'.join(local.relative_to(APPLICATIONS_DIR).parts) != desktop_id:
        raise OverrideError('Desktop file does not match its application ID.')
    return local


def _is_icon_eligible_for_deletion(asset_path: Path, expected_hash: str | None, desktop_id: str, state: dict) -> bool:
    try:
        _confined(asset_path, ICONS_DIR)
    except OverrideError:
        return False
    if asset_path.is_symlink() or not asset_path.is_file():
        return False
    if any(r.get('icon_path') == str(asset_path) for k, r in state.items() if k != desktop_id):
        return False
    if not expected_hash:
        return False
    try:
        if hashlib.sha256(asset_path.read_bytes()).hexdigest() != expected_hash:
            return False
    except OSError:
        return False
    return True


def check_managed_icon_deletion(desktop_id: str, state: dict | None = None) -> tuple[bool, Path | None, str | None]:
    """Check whether a desktop entry has a Glyph-managed icon eligible for deletion.

    Returns (eligible, path, reason):
    - (True, path, None) if the icon file is managed by Glyph, unreferenced by other entries, and unchanged.
    - (False, None, None) if no managed icon file is recorded.
    - (False, path, "shared") if another entry references the same icon file.
    - (False, path, "modified") if the icon file was modified externally (hash mismatch).
    - (False, path, "missing") if the icon file does not exist.
    """
    if state is None:
        state = load_state()
    record = state.get(desktop_id)
    if not record or not record.get('icon_path'):
        return False, None, None
    asset = record['icon_path']
    asset_path = Path(asset)
    try:
        _confined(asset_path, ICONS_DIR)
    except OverrideError:
        return False, None, None
    if asset_path.is_symlink():
        return False, None, None
    if not asset_path.is_file():
        return False, asset_path, "missing"
    if any(r.get('icon_path') == asset for k, r in state.items() if k != desktop_id):
        return False, asset_path, "shared"
    expected_hash = record.get('icon_hash')
    if not expected_hash:
        return False, asset_path, "missing_hash"
    try:
        current_hash = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        if current_hash != expected_hash:
            return False, asset_path, "modified"
    except OSError:
        return False, asset_path, "unreadable"
    return True, asset_path, None


def can_delete_managed_icon(desktop_id: str, state: dict | None = None) -> bool:
    """Convenience check: True if desktop_id has an eligible, unreferenced, hash-matched managed icon."""
    eligible, _, _ = check_managed_icon_deletion(desktop_id, state)
    return eligible


def _prepare(state: dict, desktop_id: str, source_desktop: str, *, force: bool = False):
    _id(desktop_id)
    source = Path(source_desktop)
    if not source_desktop or not source.is_file():
        raise OverrideError('This app has no readable desktop file to override.')
    local = _local_path_for(source, desktop_id)
    text = _read(local if local.exists() else source, MAX_DESKTOP_BYTES).decode('utf-8')
    # Validate its main group without changing any content.
    _replace_lines(text, 'Name', _snapshot_lines(text, 'Name'))
    record = dict(state.get(desktop_id, {}))
    if record.get('last_hash') and record['last_hash'] != _digest(text.encode()):
        if not force:
            raise LauncherModifiedExternallyError(
                'This launcher changed outside Glyph. Export your preferences and resolve the external changes before editing it.',
                desktop_id=desktop_id,
            )
    if not record:
        record = {'created_local': not local.exists(), 'source_path': str(source),
                  'local_desktop': str(local), 'original_text': text}
    if in_flatpak():
        record['launcher_storage'] = FLATPAK_LAUNCHER_STORAGE
    return local, text, record


def _is_valid_image_header(data: bytes, suffix: str) -> bool:
    s = suffix.lower()
    if s == '.png':
        return data.startswith(b'\x89PNG\r\n\x1a\n')
    if s in ('.jpg', '.jpeg'):
        return data.startswith(b'\xff\xd8\xff')
    if s == '.webp':
        return len(data) >= 12 and data.startswith(b'RIFF') and data[8:12] == b'WEBP'
    if s == '.svg':
        sample = data[:4096].decode('utf-8', errors='ignore').lower()
        return '<svg' in sample
    return False


def validate_image(data: bytes, suffix: str) -> None:
    if suffix.lower() not in ALLOWED_SUFFIXES or not data or len(data) > MAX_IMAGE_BYTES:
        raise OverrideError('Choose a PNG, SVG, JPEG or WebP image up to 10 MiB.')
    # Decode at a bounded size; this validates actual contents, not just extension.
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib
    loader = GdkPixbuf.PixbufLoader.new()
    loader.set_size(256, 256)
    try:
        loader.write(data)
        loader.close()
        if loader.get_pixbuf() is None:
            raise OverrideError('The selected file is not a readable image.')
    except GLib.Error as exc:
        msg = str(exc.message or '')
        is_sandbox_blocked = any(
            hint in msg
            for hint in ('bwrap', 'glycin', 'Loader process exited early', 'Operation not permitted')
        )
        if is_sandbox_blocked and _is_valid_image_header(data, suffix):
            return
        raise OverrideError(f'The selected image cannot be decoded: {exc.message}') from exc


def _apply(state, desktop_id, source_desktop, *, image=None, suffix=None, name=None, theme_icon=None, command=None, force: bool = False, delete_icon: bool = True):
    local, text, record = _prepare(state, desktop_id, source_desktop, force=force)
    writes = {}

    if force and record:
        external_baseline = text
        # Refresh baseline for active overrides or the field being replaced
        ext_icon = get_icon_value(external_baseline)
        if image is not None or theme_icon is not None:
            record['original_icon'] = ext_icon
            record['original_icon_lines'] = _snapshot_lines(external_baseline, 'Icon')
        elif record.get('icon_path') or record.get('theme_icon'):
            active_icon = record.get('icon_path') or record.get('theme_icon')
            if ext_icon != active_icon:
                record['original_icon'] = ext_icon
                record['original_icon_lines'] = _snapshot_lines(external_baseline, 'Icon')

        ext_name = get_name_value(external_baseline)
        if name is not None:
            record['original_name'] = ext_name
            record['original_name_lines'] = _snapshot_lines(external_baseline, 'Name')
        elif record.get('custom_name'):
            if ext_name != record.get('custom_name'):
                record['original_name'] = ext_name
                record['original_name_lines'] = _snapshot_lines(external_baseline, 'Name')

        ext_cmd = get_exec_value(external_baseline)
        if command is not None:
            record['original_command'] = ext_cmd
            record['original_exec_lines'] = _snapshot_lines(external_baseline, 'Exec')
        elif record.get('custom_command'):
            if ext_cmd != record.get('custom_command'):
                record['original_command'] = ext_cmd
                record['original_exec_lines'] = _snapshot_lines(external_baseline, 'Exec')

        # Rebase still-active saved overrides that this operation is not replacing
        if image is None and theme_icon is None:
            if record.get('icon_path'):
                text = set_icon_value(text, record['icon_path'])
            elif record.get('theme_icon'):
                text = set_icon_value(text, record['theme_icon'])
        if name is None and record.get('custom_name'):
            text = set_name_value(text, record['custom_name'])
        if command is None and record.get('custom_command'):
            text = set_exec_value(text, record['custom_command'])

    if image is not None:
        validate_image(image, suffix)
        id_hash = hashlib.sha256(desktop_id.encode('utf-8')).hexdigest()[:16]
        img_hash = hashlib.sha256(image).hexdigest()[:16]
        dest = ICONS_DIR / f'{id_hash}-{img_hash}{suffix.lower()}'
        if not force and not record.get('icon_path') and not record.get('theme_icon'):
            record['original_icon'] = get_icon_value(text)
            record['original_icon_lines'] = _snapshot_lines(text, 'Icon')
        old = record.get('icon_path')
        old_hash = record.get('icon_hash')
        old_path_to_delete = None
        if old and old != str(dest) and delete_icon:
            old_path = Path(old)
            if _is_icon_eligible_for_deletion(old_path, old_hash, desktop_id, state):
                old_path_to_delete = old_path
        record.pop('theme_icon', None)
        record['icon_path'] = str(dest)
        record['icon_hash'] = hashlib.sha256(image).hexdigest()
        writes[dest] = image
        text = set_icon_value(text, str(dest))
        if old_path_to_delete:
            writes[old_path_to_delete] = None
    if theme_icon is not None:
        theme_icon = theme_icon.strip()
        if not theme_icon or len(theme_icon) > 256 or '/' in theme_icon or '\\' in theme_icon or any(ord(c) < 32 for c in theme_icon):
            raise OverrideError('Invalid icon theme name.')
        if not force and not record.get('icon_path') and not record.get('theme_icon'):
            record['original_icon'] = get_icon_value(text)
            record['original_icon_lines'] = _snapshot_lines(text, 'Icon')
        old = record.get('icon_path')
        old_hash = record.get('icon_hash')
        old_path_to_delete = None
        if old and delete_icon:
            old_path = Path(old)
            if _is_icon_eligible_for_deletion(old_path, old_hash, desktop_id, state):
                old_path_to_delete = old_path
        record.pop('icon_path', None)
        record.pop('icon_hash', None)
        record['theme_icon'] = theme_icon
        text = set_icon_value(text, theme_icon)
        if old_path_to_delete:
            writes[old_path_to_delete] = None
    if name is not None:
        name = name.strip()
        if not name or len(name) > 256 or any(ord(c) < 32 for c in name):
            raise OverrideError('Enter a name of 1–256 characters without control characters.')
        if not force and not record.get('custom_name'):
            record['original_name'] = get_name_value(text)
            record['original_name_lines'] = _snapshot_lines(text, 'Name')
        record['custom_name'] = name
        text = set_name_value(text, name)
    if command is not None:
        command = command.strip()
        if not command or len(command) > 1024 or any(ord(c) < 32 and c not in '\t ' for c in command):
            raise OverrideError('Enter a valid command of 1–1024 characters.')
        if not force and not record.get('custom_command'):
            record['original_command'] = get_exec_value(text)
            record['original_exec_lines'] = _snapshot_lines(text, 'Exec')
        record['custom_command'] = command
        text = set_exec_value(text, command)
    record['last_hash'] = _digest(text.encode())
    state[desktop_id] = record
    writes[local] = text.encode()
    _commit(state, writes)
    return record


@operation
def apply_icon(desktop_id: str, source_desktop: str, image_path: str, *, force: bool = False, delete_icon: bool = True) -> dict:
    if not image_path:
        raise OverrideError('The selected file is not available as a local file.')
    image = Path(image_path).expanduser()
    state = load_state()  # Fail closed before changing files.
    record = _apply(state, desktop_id, source_desktop,
                    image=_read(image, MAX_IMAGE_BYTES), suffix=image.suffix, force=force, delete_icon=delete_icon)
    refresh_desktop_database()
    return record


@operation
def apply_theme_icon(desktop_id: str, source_desktop: str, icon_name: str, *, force: bool = False, delete_icon: bool = True) -> dict:
    record = _apply(load_state(), desktop_id, source_desktop, theme_icon=icon_name, force=force, delete_icon=delete_icon)
    refresh_desktop_database()
    return record


@operation
def apply_name(desktop_id: str, source_desktop: str, new_name: str, *, force: bool = False) -> dict:
    record = _apply(load_state(), desktop_id, source_desktop, name=new_name, force=force)
    refresh_desktop_database()
    return record


@operation
def apply_command(desktop_id: str, source_desktop: str, new_command: str, *, force: bool = False) -> dict:
    record = _apply(load_state(), desktop_id, source_desktop, command=new_command, force=force)
    refresh_desktop_database()
    return record


def _revert(desktop_id: str, key: str, *, force: bool = False, delete_icon: bool = True) -> None:
    state = load_state()
    record = state.get(desktop_id)
    if key == 'Icon':
        field = 'icon_path' if (record and record.get('icon_path')) else 'theme_icon'
    elif key == 'Name':
        field = 'custom_name'
    elif key == 'Exec':
        field = 'custom_command'
    else:
        raise OverrideError(f'Unknown override key: {key}')

    if not record or not record.get(field):
        raise OverrideError(f'No custom {key.lower()} override for this app.')
    local, text, record = _prepare(state, desktop_id, record['local_desktop'], force=force)

    saved_key = 'exec' if key == 'Exec' else key.lower()
    if force and record:
        external_baseline = text
        # Restore the field being reverted from the current external baseline
        saved = _snapshot_lines(external_baseline, key)
        # Refresh baselines for other still-active overrides only if externally changed
        ext_icon = get_icon_value(external_baseline)
        if key != 'Icon' and (record.get('icon_path') or record.get('theme_icon')):
            active_icon = record.get('icon_path') or record.get('theme_icon')
            if ext_icon != active_icon:
                record['original_icon'] = ext_icon
                record['original_icon_lines'] = _snapshot_lines(external_baseline, 'Icon')

        ext_name = get_name_value(external_baseline)
        if key != 'Name' and record.get('custom_name'):
            if ext_name != record.get('custom_name'):
                record['original_name'] = ext_name
                record['original_name_lines'] = _snapshot_lines(external_baseline, 'Name')

        ext_cmd = get_exec_value(external_baseline)
        if key != 'Exec' and record.get('custom_command'):
            if ext_cmd != record.get('custom_command'):
                record['original_command'] = ext_cmd
                record['original_exec_lines'] = _snapshot_lines(external_baseline, 'Exec')

        # Rebase still-active saved overrides that this operation is not reverting
        if key != 'Icon':
            if record.get('icon_path'):
                text = set_icon_value(text, record['icon_path'])
            elif record.get('theme_icon'):
                text = set_icon_value(text, record['theme_icon'])
        if key != 'Name' and record.get('custom_name'):
            text = set_name_value(text, record['custom_name'])
        if key != 'Exec' and record.get('custom_command'):
            text = set_exec_value(text, record['custom_command'])
    else:
        saved = record.get('original_' + saved_key + '_lines')
        if saved is None:
            source = Path(record.get('source_path', ''))
            if record.get('created_local') and source != local and source.is_file():
                saved = _snapshot_lines(_read(source, MAX_DESKTOP_BYTES).decode('utf-8'), key)
            else:
                value = record.get('original_' + saved_key, '')
                saved = [f'{key}={_escape(value)}\n'] if value else []

    text = _replace_lines(text, key, saved)
    writes = {local: text.encode()}
    asset = record.get('icon_path') if key == 'Icon' else None
    asset_hash = record.get('icon_hash') if key == 'Icon' else None
    icon_to_delete = None
    if asset and delete_icon:
        asset_path = Path(asset)
        if _is_icon_eligible_for_deletion(asset_path, asset_hash, desktop_id, state):
            icon_to_delete = asset_path

    fields_to_pop = [field, 'original_' + saved_key, 'original_' + saved_key + '_lines']
    if key == 'Icon':
        fields_to_pop.extend(['icon_path', 'icon_hash', 'theme_icon'])
    for name in fields_to_pop:
        record.pop(name, None)
    has_overrides = bool(record.get('icon_path') or record.get('theme_icon') or record.get('custom_name') or record.get('custom_command'))
    if not has_overrides and not record.get('glyph_managed_appimage'):
        if record.get('created_local'):
            stock_text = None
            src_path = Path(record.get('source_path', ''))
            if not (src_path.is_file() and src_path != local):
                stock_found = find_stock(desktop_id)
                if stock_found:
                    src_path = Path(stock_found)
            if src_path.is_file() and src_path != local:
                try:
                    stock_text = _read(src_path, MAX_DESKTOP_BYTES).decode('utf-8', errors='replace')
                except Exception:
                    stock_text = None

            original = stock_text if stock_text is not None else record.get('original_text')
            if original is not None:
                def normalized(value):
                    v = _replace_lines(_replace_lines(_replace_lines(value, 'Icon', []), 'Name', []), 'Exec', [])
                    return v, _snapshot_lines(value, 'Icon'), _snapshot_lines(value, 'Name'), _snapshot_lines(value, 'Exec')
                if normalized(text) == normalized(original):
                    writes[local] = None
        del state[desktop_id]
    else:
        record['last_hash'] = _digest(text.encode())
        state[desktop_id] = record
    if icon_to_delete:
        writes[icon_to_delete] = None
    _commit(state, writes)
    refresh_desktop_database()


@operation
def revert_icon(desktop_id: str, *, force: bool = False, delete_icon: bool = True) -> None:
    _revert(desktop_id, 'Icon', force=force, delete_icon=delete_icon)


@operation
def revert_name(desktop_id: str, *, force: bool = False) -> None:
    _revert(desktop_id, 'Name', force=force)


@operation
def revert_command(desktop_id: str, *, force: bool = False) -> None:
    _revert(desktop_id, 'Exec', force=force)


@operation
def restore_stock_launcher(desktop_id: str, *, force: bool = False, delete_icon: bool = True) -> None:
    _id(desktop_id)
    if not find_stock(desktop_id):
        raise OverrideError('No stock launcher exists. This local-only application was left unchanged.')
    state = load_state()
    record = state.get(desktop_id)
    if not record:
        raise OverrideError('Glyph has no recorded override for this launcher; it was left unchanged.')
    local = Path(record.get('local_desktop', ''))
    _confined(local, APPLICATIONS_DIR)
    if '-'.join(local.relative_to(APPLICATIONS_DIR).parts) != desktop_id:
        raise OverrideError('The recorded launcher path does not match this application; it was left unchanged.')
    if not local.is_file():
        raise OverrideError('No local launcher override exists.')

    if not record.get('created_local'):
        raise OverrideError(
            'This user launcher existed before Glyph and was left in place. '
            'Use the name, icon, and command reset controls to undo Glyph’s changes.')

    current = _read(local, MAX_DESKTOP_BYTES)
    expected_hash = record.get('last_hash', '')
    if not expected_hash or _digest(current) != expected_hash:
        if not force:
            raise LauncherModifiedExternallyError(
                'The local launcher is not the unchanged copy recorded by Glyph. '
                'It was left in place to preserve your changes.',
                desktop_id=desktop_id,
            )

    writes: dict[Path, bytes | None] = {local: None}
    if delete_icon:
        eligible, asset_path, _ = check_managed_icon_deletion(desktop_id, state)
        if eligible and asset_path:
            writes[asset_path] = None
    state.pop(desktop_id)
    _commit(state, writes)
    refresh_desktop_database()


@operation
def discard_override(desktop_id: str, *, delete_icon: bool = True) -> dict[str, Any]:
    """Discard Glyph's tracked overrides for a desktop entry without altering unrelated lines."""
    _id(desktop_id)
    state = load_state()
    record = state.pop(desktop_id, None)
    if not record:
        return {'icon_deleted': False, 'icon_skipped': False, 'icon_skip_reason': None, 'icon_kept': False}
    writes: dict[Path, bytes | None] = {}
    icon_deleted = False
    icon_skipped = False
    icon_skip_reason = None
    icon_kept = False

    asset = record.get('icon_path')
    asset_path = Path(asset) if asset else None
    asset_exists = False
    if asset_path:
        try:
            _confined(asset_path, ICONS_DIR)
            if not asset_path.is_symlink() and asset_path.is_file():
                asset_exists = True
        except Exception:
            asset_exists = False

    if not delete_icon:
        if asset_exists:
            icon_kept = True
    elif asset_path:
        temp_state = {desktop_id: record, **state}
        eligible, m_path, reason = check_managed_icon_deletion(desktop_id, temp_state)
        if not eligible:
            if asset_exists and reason in ('modified', 'missing_hash', 'shared', 'unreadable'):
                icon_skipped = True
                icon_skip_reason = reason
        elif eligible and m_path:
            local_str = record.get('local_desktop')
            local = Path(local_str) if local_str else None
            needs_launcher_rewrite = False
            new_launcher_bytes = None

            if local and local.is_file():
                try:
                    _confined(local, APPLICATIONS_DIR)
                    text = _read(local, MAX_DESKTOP_BYTES).decode('utf-8')
                    if get_icon_value(text) == str(m_path):
                        # Determine replacement in order: stock icon, then saved original icon
                        stock_icon = ""
                        stock_path = find_stock(desktop_id)
                        if stock_path and Path(stock_path).is_file():
                            try:
                                stock_text = _read(Path(stock_path), MAX_DESKTOP_BYTES).decode('utf-8')
                                stock_icon = get_icon_value(stock_text)
                            except Exception:
                                stock_icon = ""
                        if not stock_icon:
                            orig = record.get('original_icon', '')
                            if orig and orig != str(m_path):
                                stock_icon = orig

                        if stock_icon and stock_icon != str(m_path):
                            new_text = set_icon_value(text, stock_icon)
                        else:
                            # If neither provides a replacement, remove only the Icon= key
                            new_text = _replace_lines(text, 'Icon', [])

                        needs_launcher_rewrite = True
                        new_launcher_bytes = new_text.encode('utf-8')
                except Exception:
                    # If the launcher cannot be read or safely rewritten, preserve the image and skip cleanup
                    icon_skipped = True
                    icon_skip_reason = 'unreadable_launcher'

            if not icon_skipped:
                if needs_launcher_rewrite and local and new_launcher_bytes is not None:
                    writes[local] = new_launcher_bytes
                writes[m_path] = None
                icon_deleted = True

    _commit(state, writes)
    refresh_desktop_database()
    return {
        'icon_deleted': icon_deleted,
        'icon_skipped': icon_skipped,
        'icon_skip_reason': icon_skip_reason,
        'icon_kept': icon_kept,
    }


def _batch(ids, function) -> BatchResult:
    result = BatchResult()
    for desktop_id in ids:
        try:
            function(desktop_id)
            result.completed += 1
        except OverrideError as exc:
            result.errors[desktop_id] = str(exc)
    return result


@operation
def revert_all_icons(*, delete_icon: bool = True) -> BatchResult:
    return _batch([k for k, r in load_state().items() if r.get('icon_path') or r.get('theme_icon')], lambda did: revert_icon(did, delete_icon=delete_icon))


@operation
def revert_all_names() -> BatchResult:
    return _batch([k for k, r in load_state().items() if r.get('custom_name')], revert_name)


@operation
def restore_all_to_stock(*, delete_icon: bool = True) -> BatchResult:
    load_state()
    return _batch([k for k in desktop_index([APPLICATIONS_DIR]) if find_stock(k)], lambda did: restore_stock_launcher(did, delete_icon=delete_icon))
