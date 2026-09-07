"""Backup creation, preview, and restoration archive serialization."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
from typing import TYPE_CHECKING

from glyph.paths import data_home, desktop_index, find_stock

if TYPE_CHECKING:
    from glyph.overrides import BatchResult


@dataclass
class BackupPlan:
    entries: dict[str, dict]
    assets: dict[str, bytes]
    sources: dict[str, str]
    conflicts: list[str]
    missing: list[str]
    legacy: bool = False


def _backup_members(source: Path) -> dict[str, bytes]:
    from glyph.overrides import (
        MAX_BACKUP_BYTES,
        MAX_ENTRIES,
        MAX_IMAGE_BYTES,
        MAX_STATE_BYTES,
        OverrideError,
    )

    members = {}
    total = 0
    with tarfile.open(source, 'r:gz') as archive:
        for member in archive:
            name = member.name
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or str(path) != name:
                raise OverrideError('Backup contains an invalid path.')
            if member.isdir() and name == 'icons':
                continue  # Legacy export includes this directory entry.
            if (not member.isfile() or name in members or
                    not (name in ('manifest.json', 'overrides.json') or
                         (len(path.parts) == 2 and path.parts[0] == 'icons'))):
                raise OverrideError('Backup contains an unexpected or duplicate member.')
            total += member.size
            limit = MAX_STATE_BYTES if name.endswith('.json') else MAX_IMAGE_BYTES
            if member.size < 0 or member.size > limit or total > MAX_BACKUP_BYTES or len(members) >= MAX_ENTRIES + 1:
                raise OverrideError('Backup exceeds the supported size or entry count.')
            stream = archive.extractfile(member)
            if stream is None:
                raise OverrideError('Cannot read backup member.')
            members[name] = stream.read(limit + 1)
    return members


def export_backup(target_path: Path) -> int:
    from glyph import overrides
    from glyph.overrides import (
        MAX_BACKUP_BYTES,
        MAX_IMAGE_BYTES,
        OverrideError,
        _read,
        load_state,
    )

    @overrides.operation
    def _export() -> int:
        state = load_state()
        entries = {}
        assets = {}
        for desktop_id, record in state.items():
            entry = {}
            if record.get('custom_name'):
                entry['name'] = record['custom_name']
            if record.get('icon_path'):
                image = Path(record['icon_path'])
                data = _read(image, MAX_IMAGE_BYTES)
                asset = 'icons/' + hashlib.sha256(data).hexdigest() + image.suffix.lower()
                entry['icon'] = asset
                assets[asset] = data
            if entry:
                entries[desktop_id] = entry
        payload = {'manifest.json': json.dumps({'version': 2, 'entries': entries}, indent=2).encode(), **assets}
        if sum(map(len, payload.values())) > MAX_BACKUP_BYTES:
            raise OverrideError('Backup exceeds 100 MiB.')
        nonlocal target_path
        target_path = target_path.absolute()
        data_dir = getattr(overrides, 'DATA_DIR', data_home() / 'glyph')
        apps_dir = getattr(overrides, 'APPLICATIONS_DIR', data_home() / 'applications')
        if target_path.resolve().is_relative_to(data_dir.resolve()) or target_path.resolve().is_relative_to(apps_dir.resolve()):
            raise OverrideError('Save the backup outside Glyph data and application directories.')
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.glyph-backup-', dir=target_path.parent)
        os.close(fd)
        try:
            with tarfile.open(name, 'w:gz') as archive:
                for member, data in payload.items():
                    info = tarfile.TarInfo(member)
                    info.size = len(data)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))
            os.replace(name, target_path)
        finally:
            Path(name).unlink(missing_ok=True)
        return len(entries)

    return _export()


def preview_backup(source_path: Path) -> BackupPlan:
    from glyph import overrides
    from glyph.overrides import (
        MAX_ENTRIES,
        OverrideError,
        _id,
        load_state,
        validate_image,
    )

    @overrides.operation
    def _preview() -> BackupPlan:
        members = _backup_members(source_path)
        legacy = 'manifest.json' not in members
        manifest_name = 'overrides.json' if legacy else 'manifest.json'
        if manifest_name not in members or ('manifest.json' in members and 'overrides.json' in members):
            raise OverrideError('Backup must contain one supported manifest.')
        manifest = json.loads(members[manifest_name])
        if not isinstance(manifest, dict):
            raise OverrideError('Invalid backup manifest.')
        if legacy:
            entries = {}
            for desktop_id, record in manifest.items():
                if not isinstance(record, dict):
                    raise OverrideError('Invalid legacy backup record.')
                entry = {}
                if record.get('custom_name'):
                    entry['name'] = record['custom_name']
                if record.get('icon_path'):
                    if not isinstance(record['icon_path'], str):
                        raise OverrideError('Invalid legacy icon reference.')
                    entry['icon'] = 'icons/' + PurePosixPath(record['icon_path']).name
                entries[desktop_id] = entry
        else:
            if manifest.get('version') != 2 or set(manifest) != {'version', 'entries'}:
                raise OverrideError('Unsupported backup version.')
            entries = manifest['entries']
        if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
            raise OverrideError('Invalid backup entries.')
        sources = {}
        missing = []
        current = load_state()
        conflicts = []
        apps_dir = getattr(overrides, 'APPLICATIONS_DIR', data_home() / 'applications')
        local_index = desktop_index([apps_dir])
        for desktop_id, entry in entries.items():
            _id(desktop_id)
            if not isinstance(entry, dict) or not entry or set(entry) - {'name', 'icon'}:
                raise OverrideError('Invalid customization in backup.')
            if 'name' in entry and (not isinstance(entry['name'], str) or not entry['name'].strip()
                    or len(entry['name']) > 256 or any(ord(c) < 32 for c in entry['name'])):
                raise OverrideError('Invalid display name in backup.')
            if 'icon' in entry:
                asset = entry['icon']
                if not isinstance(asset, str) or not asset.startswith('icons/') or asset not in members:
                    raise OverrideError('Backup is missing a referenced icon.')
                validate_image(members[asset], PurePosixPath(asset).suffix)
            stock_lookup = getattr(overrides, 'find_stock', find_stock)
            source = str(local_index.get(desktop_id) or stock_lookup(desktop_id))
            if source:
                sources[desktop_id] = source
                if desktop_id in current or desktop_id in local_index:
                    conflicts.append(desktop_id)
            else:
                missing.append(desktop_id)
        return BackupPlan(entries, {k: v for k, v in members.items() if k.startswith('icons/')},
                          sources, conflicts, missing, legacy)

    return _preview()


def import_backup(source_path: Path, *, replace_existing: bool = False) -> BatchResult:
    from glyph import overrides
    from glyph.overrides import (
        BatchResult,
        OverrideError,
        _apply,
        load_state,
        refresh_desktop_database,
    )

    @overrides.operation
    def _import() -> BatchResult:
        plan = preview_backup(source_path)
        result = BatchResult(skipped=list(plan.missing))
        for desktop_id, entry in plan.entries.items():
            if desktop_id not in plan.sources:
                continue
            if desktop_id in plan.conflicts and not replace_existing:
                result.skipped.append(desktop_id)
                continue
            try:
                asset = entry.get('icon')
                _apply(load_state(), desktop_id, plan.sources[desktop_id],
                       image=plan.assets[asset] if asset else None,
                       suffix=PurePosixPath(asset).suffix if asset else None, name=entry.get('name'))
                result.completed += 1
            except OverrideError as exc:
                result.errors[desktop_id] = str(exc)
        refresh_desktop_database()
        return result

    return _import()
