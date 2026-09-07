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
    get_icon_value,
    get_name_value,
    set_icon_value,
    set_name_value,
)
from glyph.paths import data_home, desktop_index, find_stock

XDG_DATA_HOME = data_home()
APPLICATIONS_DIR = XDG_DATA_HOME / 'applications'
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
    return DATA_DIR / 'transaction.json'


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
                                    'Keep transaction.json and resolve this conflict before editing.')
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
        raise OverrideError(f'Cannot recover transaction.json: {exc}. Keep this file for recovery.') from exc


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
                    'icon_path', 'original_text', 'last_hash'):
            if key in record and not isinstance(record[key], str):
                raise OverrideError(f'Invalid {key} in override state.')
        if type(record.get('created_local', False)) is not bool:
            raise OverrideError('Invalid ownership in override state.')
        local = _confined(Path(record.get('local_desktop', '')), APPLICATIONS_DIR)
        if '-'.join(local.relative_to(APPLICATIONS_DIR).parts) != desktop_id:
            raise OverrideError(f'Launcher path does not match {desktop_id}. Keep overrides.json for recovery.')
        if record.get('icon_path'):
            _confined(Path(record['icon_path']), ICONS_DIR)
        for key in ('Name', 'Icon'):
            saved = record.get('original_' + key.lower() + '_lines')
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


def _prepare(state: dict, desktop_id: str, source_desktop: str):
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
        raise OverrideError('This launcher changed outside Glyph. Export your preferences and resolve the external changes before editing it.')
    if not record:
        record = {'created_local': not local.exists(), 'source_path': str(source),
                  'local_desktop': str(local), 'original_text': text}
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


def _apply(state, desktop_id, source_desktop, *, image=None, suffix=None, name=None):
    local, text, record = _prepare(state, desktop_id, source_desktop)
    writes = {}
    if image is not None:
        validate_image(image, suffix)
        id_hash = hashlib.sha256(desktop_id.encode('utf-8')).hexdigest()[:16]
        img_hash = hashlib.sha256(image).hexdigest()[:16]
        dest = ICONS_DIR / f'{id_hash}-{img_hash}{suffix.lower()}'
        if not record.get('icon_path'):
            record['original_icon'] = get_icon_value(text)
            record['original_icon_lines'] = _snapshot_lines(text, 'Icon')
        old = record.get('icon_path')
        record['icon_path'] = str(dest)
        writes[dest] = image
        text = set_icon_value(text, str(dest))
        if old and old != str(dest) and not any(r.get('icon_path') == old for k, r in state.items() if k != desktop_id):
            writes[Path(old)] = None
    if name is not None:
        name = name.strip()
        if not name or len(name) > 256 or any(ord(c) < 32 for c in name):
            raise OverrideError('Enter a name of 1–256 characters without control characters.')
        if not record.get('custom_name'):
            record['original_name'] = get_name_value(text)
            record['original_name_lines'] = _snapshot_lines(text, 'Name')
        record['custom_name'] = name
        text = set_name_value(text, name)
    record['last_hash'] = _digest(text.encode())
    state[desktop_id] = record
    writes[local] = text.encode()
    _commit(state, writes)
    return record


@operation
def apply_icon(desktop_id: str, source_desktop: str, image_path: str) -> dict:
    if not image_path:
        raise OverrideError('The selected file is not available as a local file.')
    image = Path(image_path).expanduser()
    state = load_state()  # Fail closed before changing files.
    record = _apply(state, desktop_id, source_desktop,
                    image=_read(image, MAX_IMAGE_BYTES), suffix=image.suffix)
    refresh_desktop_database()
    return record


@operation
def apply_name(desktop_id: str, source_desktop: str, new_name: str) -> dict:
    record = _apply(load_state(), desktop_id, source_desktop, name=new_name)
    refresh_desktop_database()
    return record


def _revert(desktop_id: str, key: str) -> None:
    state = load_state()
    record = state.get(desktop_id)
    field = 'icon_path' if key == 'Icon' else 'custom_name'
    if not record or not record.get(field):
        raise OverrideError(f'No custom {key.lower()} override for this app.')
    local, text, record = _prepare(state, desktop_id, record['local_desktop'])
    saved = record.get('original_' + key.lower() + '_lines')
    if saved is None:
        # Older Glyph state did not retain translations. Recover them from its
        # recorded stock source only when Glyph originally created the override.
        source = Path(record.get('source_path', ''))
        if record.get('created_local') and source != local and source.is_file():
            saved = _snapshot_lines(_read(source, MAX_DESKTOP_BYTES).decode('utf-8'), key)
        else:
            value = record.get('original_' + key.lower(), '')
            saved = [f'{key}={_escape(value)}\n'] if value else []
    text = _replace_lines(text, key, saved)
    writes = {local: text.encode()}
    asset = record.get('icon_path') if key == 'Icon' else None
    for name in (field, 'original_' + key.lower(), 'original_' + key.lower() + '_lines'):
        record.pop(name, None)
    if not record.get('icon_path') and not record.get('custom_name'):
        # Remove only an unchanged Glyph-created copy. Compare key order
        # independently, since key replacement can reposition Name and Icon.
        if record.get('created_local'):
            original = record.get('original_text')
            if original is None:
                src_path = Path(record.get('source_path', ''))
                if not (src_path.is_file() and src_path != local):
                    stock_found = find_stock(desktop_id)
                    if stock_found:
                        src_path = Path(stock_found)
                if src_path.is_file() and src_path != local:
                    try:
                        original = _read(src_path, MAX_DESKTOP_BYTES).decode('utf-8', errors='replace')
                    except Exception:
                        original = None
            if original is not None:
                def normalized(value):
                    return _replace_lines(_replace_lines(value, 'Icon', []), 'Name', []), _snapshot_lines(value, 'Icon'), _snapshot_lines(value, 'Name')
                if normalized(text) == normalized(original):
                    writes[local] = None
        del state[desktop_id]
    else:
        record['last_hash'] = _digest(text.encode())
        state[desktop_id] = record
    if asset and not any(r.get('icon_path') == asset for r in state.values()):
        writes[Path(asset)] = None
    _commit(state, writes)
    refresh_desktop_database()


@operation
def revert_icon(desktop_id: str) -> None:
    _revert(desktop_id, 'Icon')


@operation
def revert_name(desktop_id: str) -> None:
    _revert(desktop_id, 'Name')


@operation
def restore_stock_launcher(desktop_id: str) -> None:
    _id(desktop_id)
    if not find_stock(desktop_id):
        raise OverrideError('No stock launcher exists. This local-only application was left unchanged.')
    state = load_state()
    # Remove all local paths with this ID, including an ambiguous nested/flat pair.
    paths = [p for p in APPLICATIONS_DIR.rglob('*.desktop')
             if '-'.join(p.relative_to(APPLICATIONS_DIR).parts) == desktop_id and p.is_file()]
    if not paths:
        raise OverrideError('No local launcher override exists.')
    writes = {_confined(p, APPLICATIONS_DIR): None for p in paths}
    record = state.pop(desktop_id, {})
    asset = record.get('icon_path')
    if asset and not any(r.get('icon_path') == asset for r in state.values()):
        writes[Path(asset)] = None
    _commit(state, writes)
    refresh_desktop_database()


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
def revert_all_icons() -> BatchResult:
    return _batch([k for k, r in load_state().items() if r.get('icon_path')], revert_icon)


@operation
def revert_all_names() -> BatchResult:
    return _batch([k for k, r in load_state().items() if r.get('custom_name')], revert_name)


@operation
def restore_all_to_stock() -> BatchResult:
    load_state()
    return _batch([k for k in desktop_index([APPLICATIONS_DIR]) if find_stock(k)], restore_stock_launcher)

